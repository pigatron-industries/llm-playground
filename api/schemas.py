"""Pydantic request/response models shared by the API and the UI."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant"]


class Message(BaseModel):
    role: Role
    content: str


class ModelsResponse(BaseModel):
    provider: str
    models: list[str]


class ProviderInfo(BaseModel):
    name: str
    base_url: str


# --- Stored chats ----------------------------------------------------------


class Chat(BaseModel):
    """A full conversation, persisted server-side."""

    id: str
    title: str
    model: str | None = None
    created_at: datetime
    updated_at: datetime
    messages: list[Message] = Field(default_factory=list)


class ChatSummary(BaseModel):
    """Lightweight chat listing (no message bodies)."""

    id: str
    title: str
    model: str | None = None
    created_at: datetime
    updated_at: datetime
    message_count: int


class CreateChatRequest(BaseModel):
    title: str | None = None
    model: str | None = None


class SendMessageRequest(BaseModel):
    content: str
    model: str
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
