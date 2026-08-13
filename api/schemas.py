"""Pydantic request/response models shared by the API and the UI."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: str


class Message(BaseModel):
    role: Role
    content: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class ModelsResponse(BaseModel):
    provider: str
    models: list[str]
    # Best-effort map of model id -> context window (tokens). Empty/absent
    # entries mean the provider didn't expose a value (shown as "unknown").
    context_lengths: dict[str, int] = Field(default_factory=dict)


class ProviderInfo(BaseModel):
    name: str
    base_url: str


class WorkflowInfo(BaseModel):
    """Describes a registered workflow and the settings it accepts.

    ``settings_schema`` is the JSON Schema of the workflow's settings model
    (see ``api.workflows.base.Workflow``) — the UI renders a form from it
    rather than hard-coding inputs per workflow.

    ``has_state`` indicates whether the workflow provides a state panel
    (e.g. a map or status display) that the UI should render after messages.
    """

    id: str
    name: str
    description: str
    settings_schema: dict[str, Any]
    has_state: bool = False


# --- Stored chats ----------------------------------------------------------


class Chat(BaseModel):
    """A full conversation, persisted server-side."""

    id: str
    title: str
    workflow_id: str = "simple_chat"
    workflow_settings: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    messages: list[Message] = Field(default_factory=list)


class ChatSummary(BaseModel):
    """Lightweight chat listing (no message bodies)."""

    id: str
    title: str
    workflow_id: str
    created_at: datetime
    updated_at: datetime
    message_count: int
    # True while a response is generating in the background for this chat —
    # set by the route from the live stream registry, not stored on disk.
    is_streaming: bool = False


class CreateChatRequest(BaseModel):
    title: str | None = None
    workflow_id: str
    workflow_settings: dict[str, Any] = Field(default_factory=dict)


class UpdateChatRequest(BaseModel):
    """Partial update for an existing chat.

    ``workflow_id`` can only change while the chat has no messages yet (the
    UI locks that control after the first send). ``workflow_settings`` can be
    updated at any time and takes effect on the chat's next turn. When both
    are provided, ``workflow_settings`` is validated against the *new*
    workflow — the caller must supply settings that satisfy that workflow's
    schema, not the old one's.
    """

    title: str | None = None
    workflow_id: str | None = None
    workflow_settings: dict[str, Any] | None = None


class Project(BaseModel):
    id: str
    name: str
    path: str
    # Last-used workflow for this project, so new chats started while this
    # project is selected default to it instead of the generic fallback.
    default_workflow_id: str | None = None
    default_workflow_settings: dict[str, Any] = Field(default_factory=dict)


class CreateProjectRequest(BaseModel):
    name: str
    path: str


class UpdateProjectRequest(BaseModel):
    name: str | None = None
    path: str | None = None
    default_workflow_id: str | None = None
    default_workflow_settings: dict[str, Any] | None = None


class SendMessageRequest(BaseModel):
    """A new user turn. Model/temperature/system prompt/tools all live on the
    chat's workflow settings now, not per-message."""

    content: str


class ContextEstimate(BaseModel):
    """Best-effort size of the extra context (system prompt, injected files,
    ...) a workflow adds beyond the visible chat history — see
    ``api.workflows.base.Workflow.extra_context_chars``."""

    extra_context_chars: int
