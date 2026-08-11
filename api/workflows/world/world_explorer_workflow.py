"""The default workflow: a plain back-and-forth chat with tools."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
import re

from pydantic import BaseModel, ConfigDict, Field

from ...config import get_chats_dir
from ...providers import ChatEvent, get_client
from ...schemas import Chat, Message
from ...tools import get_tools
from ..base import Workflow, WorkflowContext
from ..common import history_for_model
from ..registry import register_workflow
from .world_schema import Character, Location, World

TOOL_TYPES = ["Math", "World"]


_WORLD_CACHE: dict[str, tuple[Path, float, World]] = {}


def _slugify_world_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "world"


def _default_world_path(world_name: str) -> Path:
    return get_chats_dir().parent / "worlds" / f"{_slugify_world_name(world_name)}.json"


def _new_world(world_name: str, world_description: str) -> World:
    start_location = Location(
        id="start",
        footprint=[(0, 0)],
        name="Trailhead",
        description=(
            world_description.strip()
            or "You stand at a quiet trailhead where worn stone markers point into unknown lands."
        ),
    )
    player = Character(
        id="player",
        name="Player",
        description="An explorer stepping into the unknown.",
        location_id="start",
    )
    world = World(locations={start_location.id: start_location}, player=player)
    if world_description.strip():
        world.event_log.append(f"World premise: {world_name} — {world_description.strip()}")
    return world


def _load_or_create_world(world_name: str, world_description: str) -> tuple[Path, World]:
    cache_key = _slugify_world_name(world_name)
    path = _default_world_path(world_name)
    cache_entry = _WORLD_CACHE.get(cache_key)

    if path.exists():
        mtime = path.stat().st_mtime
        if cache_entry and cache_entry[0] == path and cache_entry[1] == mtime:
            return path, cache_entry[2]
        world = World.load_from_file(path)
        _WORLD_CACHE[cache_key] = (path, mtime, world)
        return path, world

    world = _new_world(world_name, world_description)
    world.save_to_file(path)
    mtime = path.stat().st_mtime
    _WORLD_CACHE[cache_key] = (path, mtime, world)
    return path, world


class WorldExplorerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(json_schema_extra={"widget": "model_select"})
    world_name: str = Field(
        default="New World",
        description="Human-readable name of the world. Used for the default world file name.",
        json_schema_extra={"widget": "input"},
    )
    world_description: str = Field(
        default="",
        description="Optional description used to seed a newly created world.",
        json_schema_extra={"widget": "textarea"},
    )
    system_prompt: str = Field(
        default="",
        description="Optional instructions for the assistant",
        json_schema_extra={"widget": "textarea"},
    )
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    preserve_tool_results: bool = Field(
        default=False,
        description="Keep tool call results in the conversation sent to the model for full context",
    )


@register_workflow
class WorldExplorerWorkflow(Workflow):
    id = "world_explorer"
    name = "World explorer"
    description = "Explore a world"
    settings_model = WorldExplorerSettings

    async def run(self, ctx: WorkflowContext) -> AsyncIterator[ChatEvent]:
        settings = WorldExplorerSettings.model_validate(ctx.chat.workflow_settings)
        world_path, world = _load_or_create_world(
            settings.world_name,
            settings.world_description,
        )

        conversation: list[Message] = []
        if settings.system_prompt:
            conversation.append(Message(role="system", content=settings.system_prompt))
        conversation.append(
            Message(
                role="system",
                content=(
                    f"World file: {world_path}\n"
                    "Current world state JSON (authoritative):\n"
                    f"{world.model_dump_json(indent=2)}"
                ),
            )
        )
        conversation.extend(history_for_model(ctx.chat.messages, preserve_tool_results=settings.preserve_tool_results))
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
        settings = WorldExplorerSettings.model_validate(chat.workflow_settings)
        return len(settings.system_prompt)
