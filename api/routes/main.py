"""FastAPI routes — the functions the UI calls."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from openai import APIConnectionError, APIStatusError
from pydantic import ValidationError

from ..config import get_images_dir
from ..project_store import get_project_store
from ..providers import get_client
from ..schemas import (
    Chat,
    ChatSummary,
    ContextEstimate,
    CreateChatRequest,
    CreateProjectRequest,
    UpdateProjectRequest,
    Message,
    ModelsResponse,
    Project,
    ProviderInfo,
    RerunImageRequest,
    SendMessageRequest,
    UpdateChatRequest,
    WorkflowInfo,
)
from ..service import get_active_stream, handle_send_message, list_active_stream_ids
from ..service.chat import request_stream_stop
from ..store import get_store
from ..workflows import get_workflow, list_workflows
from ..workflows.image.image_context import set_image_context, set_image_loras
from ..workflows.image.image_tools import generate_image, list_available_models
from ..workflows.image.image_tools import list_available_loras
from ..workflows.image.image_workflow import ImageSettings

router = APIRouter(prefix="/api")
log = logging.getLogger("llm_harness.routes")


@router.get("/provider", response_model=ProviderInfo)
def provider_info() -> ProviderInfo:
    cfg = get_client().config
    return ProviderInfo(name=cfg.name, base_url=cfg.base_url)


@router.get("/models", response_model=ModelsResponse)
async def list_models() -> ModelsResponse:
    client = get_client()
    try:
        models = await client.list_models()
    except APIConnectionError as exc:
        log.exception("Could not reach provider at %s", client.config.base_url)
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach provider at {client.config.base_url}: {exc}",
        ) from exc
    except APIStatusError as exc:
        log.exception("Provider returned an error listing models")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    context_lengths = await client.model_context_lengths()
    return ModelsResponse(
        provider=client.config.name, models=models, context_lengths=context_lengths
    )


@router.get("/workflows", response_model=list[WorkflowInfo])
def list_available_workflows() -> list[WorkflowInfo]:
    return list_workflows()


@router.get("/image/models")
async def list_image_models(base: str | None = None) -> dict:
    """List image-generation models on the external image API, optionally
    filtered by ``base`` (e.g. ``flux``, ``sdxl_1_0``). Proxies the image
    API's ``GET /api/models`` so the UI's model dropdown can be populated for
    a chosen base model. ``502`` if the image API is unreachable."""
    try:
        models = await asyncio.to_thread(list_available_models, base)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"base": base, "models": models}


@router.get("/image/loras")
async def list_image_loras(base: str | None = None) -> dict:
    """List LoRAs on the external image API, optionally filtered by base.

    Proxies the image API's ``GET /api/loras`` so the UI can populate a
    LoRA picker for the chosen base model. Returns ``502`` if the image API
    is unreachable.
    """
    try:
        loras = await asyncio.to_thread(list_available_loras, base)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"base": base, "loras": loras}


@router.get("/images/{filename}")
def get_generated_image(filename: str) -> FileResponse:
    """Serve a generated image by filename (``/api/images/<file>``).

    The ``generate_image`` tool reports these URLs in its result so the UI can
    render the image inline; the file itself lives in the shared images
    directory (see ``get_images_dir``)."""
    directory = get_images_dir().resolve()
    path = (directory / filename).resolve()
    if path.parent != directory or not path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path)


# --- Stored chats ----------------------------------------------------------


@router.get("/projects", response_model=list[Project])
def list_projects() -> list[Project]:
    return get_project_store().list()


@router.post("/projects", response_model=Project)
def create_project(req: CreateProjectRequest) -> Project:
    return get_project_store().create(name=req.name, path=req.path)


@router.patch("/projects/{project_id}", response_model=Project)
def update_project(project_id: str, req: UpdateProjectRequest) -> Project:
    updated = get_project_store().update(
        project_id,
        name=req.name,
        path=req.path,
        default_workflow_id=req.default_workflow_id,
        default_workflow_settings=req.default_workflow_settings,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return updated


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str) -> None:
    if not get_project_store().delete(project_id):
        raise HTTPException(status_code=404, detail="Project not found")


@router.post("/chats", response_model=Chat)
def create_chat(req: CreateChatRequest) -> Chat:
    try:
        workflow = get_workflow(req.workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        settings = workflow.settings_model.model_validate(req.workflow_settings)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc
    return get_store().create(
        title=req.title,
        workflow_id=req.workflow_id,
        workflow_settings=settings.model_dump(),
    )


@router.patch("/chats/{chat_id}", response_model=Chat)
def update_chat(chat_id: str, req: UpdateChatRequest) -> Chat:
    """Update a chat's title, workflow and/or its settings.

    ``workflow_id`` may only change while the chat has no messages yet (the
    UI locks that control after the first send — see ``ui/chat.py``).
    ``workflow_settings`` can change at any time and takes effect on the next
    turn. Switching workflow requires supplying fresh settings for the new
    workflow's schema in the same request — the old settings won't validate
    against a different workflow.
    ``title`` can be changed at any time.
    """
    store = get_store()
    chat = store.get(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")

    workflow_id = chat.workflow_id
    if req.workflow_id is not None and req.workflow_id != chat.workflow_id:
        if chat.messages:
            raise HTTPException(
                status_code=400, detail="Cannot change workflow after the first message."
            )
        if req.workflow_settings is None:
            raise HTTPException(
                status_code=400,
                detail="workflow_settings must be provided when changing workflow_id.",
            )
        workflow_id = req.workflow_id

    try:
        workflow = get_workflow(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    settings_input = (
        req.workflow_settings if req.workflow_settings is not None else chat.workflow_settings
    )
    # Settings the workflow manages itself (marked "hidden" in its schema —
    # e.g. the image workflow's last-generation params, written by its generate
    # tool) keep their stored value: the UI form neither shows nor edits them,
    # so a form update must not clobber them with stale or empty defaults.
    merged = dict(settings_input)
    for name in workflow.hidden_settings_fields():
        if name in chat.workflow_settings:
            merged[name] = chat.workflow_settings[name]
    try:
        settings = workflow.settings_model.model_validate(merged)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc

    return store.update(
        chat_id,
        title=req.title,
        workflow_id=workflow_id,
        workflow_settings=settings.model_dump(),
    )


@router.get("/chats", response_model=list[ChatSummary])
def list_chats(project_id: str | None = None) -> list[ChatSummary]:
    chats = get_store().list(project_id=project_id)
    active_ids = list_active_stream_ids()
    for chat in chats:
        chat.is_streaming = chat.id in active_ids
    return chats


@router.get("/chats/{chat_id}", response_model=Chat)
def get_chat(chat_id: str) -> Chat:
    chat = get_store().get(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


@router.get("/chats/{chat_id}/context_estimate", response_model=ContextEstimate)
def get_context_estimate(chat_id: str) -> ContextEstimate:
    """Estimated size of whatever extra context the chat's workflow injects
    (system prompt, project files, ...) — for the UI's context-usage bar.
    Best-effort: any failure just reports 0 rather than breaking the chat."""
    chat = get_store().get(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    try:
        workflow = get_workflow(chat.workflow_id)
        chars = workflow.extra_context_chars(chat)
    except Exception:  # noqa: BLE001
        chars = 0
    return ContextEstimate(extra_context_chars=chars)


@router.delete("/chats/{chat_id}", status_code=204)
def delete_chat(chat_id: str) -> None:
    if not get_store().delete(chat_id):
        raise HTTPException(status_code=404, detail="Chat not found")


@router.post("/chats/{chat_id}/messages")
async def send_message(chat_id: str, req: SendMessageRequest) -> StreamingResponse:
    """Stream the assistant reply as newline-delimited JSON events.

    Each line is one of:
      {"type": "delta", "content": "..."}              incremental text
      {"type": "tool_call", "name": "...", "arguments": {...}}
      {"type": "tool_result", "name": "...", "result": "..."}
      {"type": "done",  "chat": {...}}                 final persisted chat
      {"type": "stopped",  "chat": {...}}              user stopped stream
      {"type": "error", "detail": "..."}               provider failure (mid-stream)

    The user + assistant messages are persisted only after a successful
    completion, so a failed stream leaves the stored chat untouched.
    """
    store = get_store()
    chat = store.get(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")

    async def events() -> AsyncIterator[str]:
        try:
            async for event in handle_send_message(chat_id, req):
                yield event
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Chat not found") from exc

    return StreamingResponse(events(), media_type="application/x-ndjson")


@router.post("/chats/{chat_id}/rerun-image", response_model=Chat)
async def rerun_image(chat_id: str, req: RerunImageRequest) -> Chat:
    """Regenerate an image with the exact parameters recorded on a previous
    one — prompt, negative prompt, width and height all come from the
    image's own metadata (the UI's Rerun button), so no LLM round-trip is
    needed. The base model / image model / LoRAs come from the chat's
    current image-workflow settings. The result is appended to the chat's
    history as a tool message — the same shape a normal ``generate_image``
    result has, so it renders as its own image bubble — and the updated
    chat is returned."""
    store = get_store()
    chat = store.get(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    if chat.workflow_id != "image":
        raise HTTPException(status_code=400, detail="Rerun is only available in the image workflow.")
    if get_active_stream(chat_id) is not None:
        raise HTTPException(status_code=409, detail="A response is still generating for this chat.")

    # Resolve the image context the same way the workflow's turn would, so
    # the tool call below uses the chat's selected base/model/LoRAs. The
    # ``model`` (chat LLM) field is required by the schema but irrelevant
    # here — tolerate a settings dict missing it rather than failing the
    # rerun over something we don't use.
    try:
        settings = ImageSettings.model_validate(chat.workflow_settings)
    except ValidationError:
        try:
            settings = ImageSettings.model_validate({**chat.workflow_settings, "model": ""})
        except ValidationError:
            settings = ImageSettings.model_validate({"model": ""})
    # ``chat_id`` lets the tool record this rerun as the chat's new
    # last-generation params (the Rerun button regenerates an exact prior set).
    set_image_context(
        base=settings.image_base, model=settings.image_model or None, chat_id=chat_id
    )
    set_image_loras(settings.selected_loras or None)

    result = await generate_image(
        prompt=req.prompt,
        negprompt=req.negative_prompt,
        width=req.width or 512,
        height=req.height or 512,
    )
    if result.startswith("Error:"):
        raise HTTPException(
            status_code=502,
            detail=result.removeprefix("Error:").strip() or "Image generation failed.",
        )

    store.add_message(chat_id, Message(role="tool", content=result))
    updated = store.get(chat_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return updated


@router.get("/chats/{chat_id}/stream")
async def reattach_stream(chat_id: str) -> StreamingResponse:
    """Reattach to a message stream that's already in progress for this
    chat, replaying whatever's been produced so far and then continuing to
    stream live — for a UI that navigated away mid-response and back.

    404 if nothing is currently streaming for this chat (the caller should
    just show the persisted chat in that case).
    """
    state = get_active_stream(chat_id)
    if state is None:
        raise HTTPException(status_code=404, detail="No stream in progress for this chat")

    return StreamingResponse(state.subscribe(), media_type="application/x-ndjson")


@router.post("/chats/{chat_id}/stop")
def stop_message(chat_id: str) -> dict:
    """Stop an in-flight message stream. Returns success status."""
    store = get_store()
    chat = store.get(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")

    success = request_stream_stop(chat_id)
    return {"stopped": success, "chat_id": chat_id}


@router.get("/chats/{chat_id}/state")
def get_chat_state(chat_id: str) -> dict:
    """Return the current state for a chat's workflow (e.g. a map or status panel).
    Delegates to the workflow's ``get_state`` method. Returns empty dict if the
    workflow does not support state output."""
    store = get_store()
    chat = store.get(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")

    workflow = get_workflow(chat.workflow_id)
    if not workflow.has_state:
        return {}

    try:
        return workflow.get_state(chat)
    except Exception:
        return {}


@router.delete("/chats/{chat_id}/messages/{index}")
def delete_message(chat_id: str, index: int) -> dict:
    """Delete a message from a chat's history by its index."""
    store = get_store()
    try:
        updated = store.remove_message(chat_id, index)
    except KeyError:
        raise HTTPException(status_code=404, detail="Chat not found")
    except ValueError:
        raise HTTPException(status_code=404, detail="Message not found")
    return {"deleted": True, "message_count": len(updated.messages)}
