"""FastAPI routes — the functions the UI calls."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from openai import APIConnectionError, APIStatusError

from .providers import get_client
from .schemas import (
    Chat,
    ChatSummary,
    CreateChatRequest,
    Message,
    ModelsResponse,
    ProviderInfo,
    SendMessageRequest,
)
from .store import get_store

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
    return ModelsResponse(provider=client.config.name, models=models)


# --- Stored chats ----------------------------------------------------------


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


@router.post("/chats/{chat_id}/messages", response_model=Chat)
async def send_message(chat_id: str, req: SendMessageRequest) -> Chat:
    store = get_store()
    chat = store.get(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")

    # Build the candidate conversation without mutating the store yet, so a
    # failed provider call leaves the stored chat untouched (safe to retry).
    user_msg = Message(role="user", content=req.content)
    conversation = chat.messages + [user_msg]

    client = get_client()
    try:
        reply = await client.chat(
            model=req.model,
            messages=conversation,
            temperature=req.temperature,
        )
    except APIConnectionError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach provider at {client.config.base_url}: {exc}",
        ) from exc
    except APIStatusError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    # Persist both sides only after a successful completion.
    store.add_message(chat_id, user_msg)
    store.add_message(chat_id, reply)
    store.set_model(chat_id, req.model)
    return store.get(chat_id)
