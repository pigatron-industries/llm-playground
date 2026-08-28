"""The image-generation workflow: a chat whose assistant can render images
via an external image API (see ``image_tools`` for the API contract).

The API base URL comes from configuration (``IMAGE_API_URL`` env var), not
from per-chat settings."""

from __future__ import annotations

from collections.abc import AsyncIterator

from pydantic import BaseModel, ConfigDict, Field

from ...config import DEFAULT_MODEL_BASES
from ...providers import ChatEvent, get_client
from ...schemas import Chat, Message
from ...tools import get_tools
from ..base import Workflow, WorkflowContext
from ..common import history_for_model
from ..registry import register_workflow

TOOL_TYPES = ["Image"]

SYSTEM_PROMPT = (
    "You are an image-generation assistant. When the user asks you to create, "
    "draw, or render an image, call the `generate_image` tool with a detailed "
    "prompt. If the user does not name a model, use the one they selected in "
    "the workflow settings (appended below). After a successful generation, "
    "tell the user the image path or URL it was saved to."
)


def _system_prompt(settings: "ImageSettings") -> str:
    """System prompt with the user's selected image model appended when set —
    so the assistant uses it for ``generate_image`` without the user restating
    it each turn. The settings form renders the base/model pickers for this."""
    prompt = SYSTEM_PROMPT
    if settings.image_model:
        base_note = f", base `{settings.image_base}`" if settings.image_base else ""
        prompt += (
            f"\n\nThe user has selected the image model `{settings.image_model}`"
            f"{base_note}. Use this model for the `generate_image` tool unless the "
            "user explicitly asks for a different one."
        )
    return prompt


class ImageSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(json_schema_extra={"widget": "model_select"})
    image_base: str = Field(
        default=DEFAULT_MODEL_BASES[0],
        description="Image base model family to generate with.",
        json_schema_extra={"widget": "image_base_select"},
    )
    image_model: str = Field(
        default="",
        description="The specific image model to generate with, chosen from the base above.",
        json_schema_extra={"widget": "image_model_select"},
    )
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)


@register_workflow
class ImageWorkflow(Workflow):
    id = "image"
    name = "Image generation"
    description = "Chat with an assistant that can generate images via an external API."
    settings_model = ImageSettings

    async def run(self, ctx: WorkflowContext) -> AsyncIterator[ChatEvent]:
        settings = ImageSettings.model_validate(ctx.chat.workflow_settings)

        conversation: list[Message] = [
            Message(role="system", content=_system_prompt(settings))
        ]
        conversation.extend(history_for_model(ctx.chat.messages))
        conversation.append(ctx.user_message)

        client = get_client()
        async for event in client.chat_stream(
            model=settings.model,
            messages=conversation,
            temperature=settings.temperature,
            tools=get_tools(tool_types=TOOL_TYPES),
        ):
            yield event

    def extra_context_chars(self, chat: Chat) -> int:
        settings = ImageSettings.model_validate(chat.workflow_settings)
        return len(_system_prompt(settings))
