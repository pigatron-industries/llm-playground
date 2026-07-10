"""The default workflow: a plain back-and-forth chat with tools."""

from __future__ import annotations

from collections.abc import AsyncIterator

from pydantic import BaseModel, ConfigDict, Field

from ..providers import ChatEvent, get_client
from ..schemas import Message
from ..tools import DEFAULT_TOOLS
from .base import Workflow, WorkflowContext
from .registry import register_workflow


class SimpleChatSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(json_schema_extra={"widget": "model_select"})
    system_prompt: str = Field(
        default="",
        description="Optional instructions for the assistant",
        json_schema_extra={"widget": "textarea"},
    )
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)


def _history_for_model(messages: list[Message]) -> list[Message]:
    """Build provider context without replaying historical tool-use entries.

    We keep tool calls/results in persisted chat history for observability,
    but avoid resending them on later turns to reduce prompt bloat.
    """
    sanitized: list[Message] = []
    for message in messages:
        if message.role == "tool":
            continue
        if message.role == "assistant" and message.tool_calls:
            if message.content:
                # Keep any visible assistant text, but strip tool-call metadata.
                sanitized.append(Message(role="assistant", content=message.content))
            continue
        sanitized.append(message)
    return sanitized


@register_workflow
class SimpleChatWorkflow(Workflow):
    id = "simple_chat"
    name = "Simple chat"
    description = "Back-and-forth chat with tools and an editable system prompt."
    settings_model = SimpleChatSettings

    async def run(self, ctx: WorkflowContext) -> AsyncIterator[ChatEvent]:
        settings = SimpleChatSettings.model_validate(ctx.chat.workflow_settings)

        conversation: list[Message] = []
        if settings.system_prompt:
            conversation.append(Message(role="system", content=settings.system_prompt))
        conversation.extend(_history_for_model(ctx.chat.messages))
        conversation.append(ctx.user_message)

        client = get_client()
        async for event in client.chat_stream(
            model=settings.model,
            messages=conversation,
            temperature=settings.temperature,
            tools=DEFAULT_TOOLS,
        ):
            yield event
