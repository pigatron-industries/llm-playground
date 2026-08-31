"""The image-generation workflow: a chat whose assistant can render images
via an external image API (see ``image_tools`` for the API contract).

The API base URL comes from configuration (``IMAGE_API_URL`` env var), not
from per-chat settings."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ...config import DEFAULT_MODEL_BASES
from ...providers import ChatEvent, get_client
from ...schemas import Chat, Message
from ...tools import get_tools
from ..base import Workflow, WorkflowContext
from ..registry import register_workflow
from .image_context import get_image_previous_prompt, set_image_context

TOOL_TYPES = ["Image"]

SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent / "templates" / "system_prompt.md"


def _last_generation_params(messages: list[Message]) -> dict | None:
    """Return the parameters (prompt, negative_prompt, width, height) of the
    most recently generated image, read from the ``[image_meta]`` line the
    ``generate_image`` tool records in the chat history. Returns ``None`` when
    no image has been generated in this chat yet.

    A single ``[image_meta]`` line holds one batch (a JSON array); all entries
    in a batch share the same prompt/negative_prompt/size, so the first entry
    carries the parameters we care about."""
    marker = "[image_meta] "
    for message in reversed(messages):
        content = message.content or ""
        index = content.find(marker)
        if index == -1:
            continue
        try:
            data = json.loads(content[index + len(marker) :].strip())
        except ValueError:
            continue
        if isinstance(data, list) and data and isinstance(data[0], dict):
            entry = data[0]
            return {
                "prompt": entry.get("prompt"),
                "negative_prompt": entry.get("negative_prompt"),
                "width": entry.get("width"),
                "height": entry.get("height"),
            }
    return None


def _system_prompt(settings: "ImageSettings", generation_params: dict | None = None) -> str:
    """System prompt from ``templates/system_prompt.md`` with the last image's
    generation parameters, the user's selected image model, and LoRAs appended
    when set — so the assistant refines the most recent image via
    ``generate_image`` without the user restating the parameters each turn.
    The settings form renders the base/model pickers for this."""
    prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    if generation_params and any(generation_params.values()):
        lines = [
            "",
            "",
            "**CURRENT GENERATION PARAMETERS**",
            "The last image you generated used these parameters. Start from them and "
            "change only what the user asks for.",
        ]
        if generation_params.get("prompt"):
            lines.append(f"Prompt: {generation_params['prompt']}")
        if generation_params.get("negative_prompt"):
            lines.append(f"Negative prompt: {generation_params['negative_prompt']}")
        if generation_params.get("width"):
            lines.append(f"Width: {generation_params['width']}")
        if generation_params.get("height"):
            lines.append(f"Height: {generation_params['height']}")
        prompt += "\n".join(lines)
    if settings.image_model:
        base_note = f", base `{settings.image_base}`" if settings.image_base else ""
        prompt += (
            f"\n\nThe user has selected the image model `{settings.image_model}`{base_note}."
        )
    if settings.selected_loras:
        lora_names = ", ".join(
            f"{entry.get('name', 'unknown')} ({entry.get('weight', 1.0)})"
            for entry in settings.selected_loras
            if isinstance(entry, dict) and entry.get("name")
        )
        if lora_names:
            prompt += f"\n\nSelected LoRAs: {lora_names}."
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
    selected_loras: list[dict] = Field(
        default_factory=list,
        description="LoRAs to apply. Each entry is an object with 'name' and 'weight', e.g. {'name': 'mylora', 'weight': 1.0}",
        json_schema_extra={"widget": "lora_select"},
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

        set_image_context(
            base=settings.image_base,
            model=settings.image_model or None,
            previous_prompt=get_image_previous_prompt(),
        )
        # Store selected LoRAs for this turn so image tools include them
        try:
            from .image_context import set_image_loras

            set_image_loras(settings.selected_loras if settings.selected_loras else None)
        except Exception:
            # Non-fatal: if context helpers not available or settings malformed
            pass

        # Only the current user turn is sent as history; the assistant gets the
        # last image's parameters (prompt, negative prompt, size) in the system
        # prompt instead of replaying the whole conversation.
        generation_params = _last_generation_params(ctx.chat.messages)
        conversation: list[Message] = [
            Message(role="system", content=_system_prompt(settings, generation_params)),
            ctx.user_message,
        ]

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
        return len(_system_prompt(settings, _last_generation_params(chat.messages)))
