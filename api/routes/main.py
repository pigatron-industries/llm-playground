"""FastAPI routes — the functions the UI calls."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from openai import APIConnectionError, APIStatusError

from ..project_store import get_project_store
from ..providers import get_client
from ..schemas import (
    Chat,
    ChatSummary,
    CreateChatRequest,
    CreateProjectRequest,
    Message,
    ModelsResponse,
    Project,
    ProviderInfo,
    SendMessageRequest,
)
from ..service import handle_send_message
from ..store import get_store

router = APIRouter(prefix="/api")


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
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach provider at {client.config.base_url}: {exc}",
        ) from exc
    except APIStatusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    context_lengths = await client.model_context_lengths()
    return ModelsResponse(
        provider=client.config.name, models=models, context_lengths=context_lengths
    )


# --- Stored chats ----------------------------------------------------------


@router.get("/projects", response_model=list[Project])
def list_projects() -> list[Project]:
    return get_project_store().list()


@router.post("/projects", response_model=Project)
def create_project(req: CreateProjectRequest) -> Project:
    return get_project_store().create(name=req.name, path=req.path)


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str) -> None:
    if not get_project_store().delete(project_id):
        raise HTTPException(status_code=404, detail="Project not found")


@router.post("/chats", response_model=Chat)
def create_chat(req: CreateChatRequest) -> Chat:
    return get_store().create(title=req.title, model=req.model)


@router.get("/chats", response_model=list[ChatSummary])
def list_chats() -> list[ChatSummary]:
    return get_store().list()


@router.get("/chats/{chat_id}", response_model=Chat)
def get_chat(chat_id: str) -> Chat:
    chat = get_store().get(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


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
