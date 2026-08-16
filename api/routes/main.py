"""FastAPI routes — the functions the UI calls."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from openai import APIConnectionError, APIStatusError
from pydantic import ValidationError

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
    SendMessageRequest,
    UpdateChatRequest,
    WorkflowInfo,
)
from ..service import get_active_stream, handle_send_message, list_active_stream_ids
from ..service.chat import request_stream_stop
from ..store import get_store
from ..workflows import get_workflow, list_workflows

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
    try:
        settings = workflow.settings_model.model_validate(settings_input)
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
