"""The default workflow: a plain back-and-forth chat with tools."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
import re

from pydantic import BaseModel, ConfigDict, Field

from ...config import get_chats_dir
from ...project_store import get_project_store
from ...providers import ChatEvent, StreamComplete, get_client
from ...schemas import Chat, Message
from ...store import get_store
from ...tools import get_tools
from ..base import Workflow, WorkflowContext
from ..common import history_for_model
from ..registry import register_workflow
from .world_schema import Character, Location, World, set_current_world

TOOL_TYPES = ["Math", "World"]


_WORLD_CACHE: dict[str, tuple[Path, World]] = {}


def _slugify_world_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "world"


def _resolve_world_path(world_name: str, project_id: str) -> Path:
    """Location of a world's folder.

    When a project is selected (and still exists), the world lives inside that
    project's folder under ``worlds/<name>/``. Otherwise it falls back to the
    shared ``data/worlds/<name>/`` directory, so world_explorer keeps working
    without a project.
    """
    slug = _slugify_world_name(world_name)
    if project_id:
        project = get_project_store().get(project_id)
        if project is not None:
            return Path(project.path) / "worlds" / slug
    return get_chats_dir().parent / "worlds" / slug


def _render_world_story(world: World) -> str:
    story_path = Path(__file__).resolve().parent / "templates" / "world_story.md"
    template = story_path.read_text(encoding="utf-8")

    location = world.locations.get(world.player.location_id)
    location_name = location.name if location is not None else "Unknown"
    location_description = location.description if location is not None else ""
    location_state = location.state if location is not None else {}
    location_footprint = ", ".join(str(cell) for cell in (location.footprint if location is not None else [])) or "None"

    exits_list = "\n".join(
        f"{direction} -> {exit.description}: leads to '{exit.destination_id}'"
        for direction, exit in (location.exits.items() if location is not None else [])
    ) or "None"

    items_list = "\n".join(
        f"- {item.id}: {item.description}"
        for item in (world.get_location_items(location.id) if location is not None else [])
    ) or "None"

    characters_list = "\n".join(
        f"- {char.name} ({char.id}): {char.notes or 'No notes.'}"
        for char in world.characters.values()
    ) or "None"

    recent_events = "\n".join(world.event_log[-10:]) or "None"
    player_inventory = ", ".join(
        f"{item.name} ({item.id})"
        for item in world.get_character_items(world.player.id)
    ) or "None"
    player_flags = "None"

    return template.format(
        world_summary=world.description,
        location_name=location_name,
        location_id=world.player.location_id,
        location_description=location_description,
        location_footprint=location_footprint,
        location_state=location_state,
        exits_list=exits_list,
        items_list=items_list,
        characters_list=characters_list,
        recent_events=recent_events,
        player_name=world.player.name,
        player_inventory=player_inventory,
        player_flags=player_flags,
    )


def _new_world(
    world_description: str,
    world_path: Path,
) -> World:
    player = Character(
        id="player",
        name="Player",
        description="An explorer stepping into the unknown.",
        inventory_ids=[],
        location_id="start",
    )
    world = World(
        description=world_description,
        locations={},
        player=player,
        event_log=[],
        characters={},
    )
    set_current_world(world, path=world_path)
    return world


async def _bootstrap_world(
    chat_id: str,
    world: World,
    world_name: str,
    world_description: str,
    world_path: Path,
    model: str,
    temperature: float,
) -> AsyncIterator[ChatEvent]:
    prompt_path = Path(__file__).resolve().parent / "templates" / "world_build_init.md"
    prompt_text = prompt_path.read_text(encoding="utf-8").format(world_description=world_description)

    conversation: list[Message] = [
        Message(role="system", content=prompt_text),
        Message(role="user", content=f"Create the first location for the world '{world_name}'"),
    ]

    client = get_client()
    async for event in client.chat_stream(
        model=model,
        messages=conversation,
        temperature=temperature,
        tools=get_tools(tool_types=TOOL_TYPES),
    ):
        if isinstance(event, StreamComplete):
            # Persist bootstrap messages from the completed stream
            for message in event.messages:
                get_store().add_message(chat_id, message)
        yield event

    if world.player.location_id not in world.locations and world.locations:
        world.player.location_id = next(iter(world.locations))

    try:
        world.save_to_file(world_path)
    except Exception:
        pass

    _WORLD_CACHE[str(world_path)] = (world_path, world)


def _new_location() -> Location:
    # TODO: prompt llm to create location and add to world
    pass


async def _load_or_create_world(
    world_name: str,
    world_description: str,
    project_id: str,
) -> tuple[Path, World, bool]:
    path = _resolve_world_path(world_name, project_id)
    cache_key = str(path)
    cache_entry = _WORLD_CACHE.get(cache_key)

    if path.exists():
        if cache_entry and cache_entry[0] == path:
            path, world = cache_entry
            set_current_world(world, path=path)
            return path, world, False
        world = World.load_from_file(path)
        set_current_world(world, path=path)
        _WORLD_CACHE[cache_key] = (path, world)
        return path, world, False

    world = _new_world(world_description, path)
    _WORLD_CACHE[cache_key] = (path, world)
    return path, world, True


class WorldExplorerSettings(BaseModel):
    model_config = ConfigDict()

    project_id: str = Field(
        default="",
        description="Project that stores the world. When set, the world file is saved under this project's folder; otherwise it falls back to the shared data/worlds/ directory.",
        json_schema_extra={"widget": "project_select"},
    )
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
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)


@register_workflow
class WorldExplorerWorkflow(Workflow):
    id = "world_explorer"
    name = "World explorer"
    description = "Explore a world"
    settings_model = WorldExplorerSettings
    has_state = True

    async def run(self, ctx: WorkflowContext) -> AsyncIterator[ChatEvent]:
        settings = WorldExplorerSettings.model_validate(ctx.chat.workflow_settings)

        # Load or create the world based on the provided name and description
        world_path, world, created = await _load_or_create_world(
            settings.world_name,
            settings.world_description,
            settings.project_id,
        )

        if created:
            async for event in _bootstrap_world(
                ctx.chat.id,
                world,
                settings.world_name,
                settings.world_description,
                world_path,
                settings.model,
                settings.temperature,
            ):
                yield event

        conversation: list[Message] = []
        conversation.append(Message(role="system", content=_render_world_story(world)))
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
        """Estimate the size of the world story injected each turn.

        world_explorer has no static system prompt — its per-turn context is the
        rendered world story — so we measure that. Best-effort: any failure
        (no world yet, missing project) reports 0 rather than raising.
        """
        settings = WorldExplorerSettings.model_validate(chat.workflow_settings)
        if not settings.world_name:
            return 0
        world_path = _resolve_world_path(settings.world_name, settings.project_id)
        try:
            world = World.load_from_file(world_path)
            return len(_render_world_story(world))
        except Exception:  # noqa: BLE001
            return 0

    def get_state(self, chat: Chat) -> dict:
        """Return current world state for the UI: player location and ASCII map."""
        settings = WorldExplorerSettings.model_validate(chat.workflow_settings)
        world_name = settings.world_name
        if not world_name:
            return {}

        world_path = _resolve_world_path(world_name, settings.project_id)

        try:
            world = World.load_from_file(world_path)
            location = world.locations.get(world.player.location_id)
            return {
                "location_id": world.player.location_id,
                "location_name": location.name if location is not None else "Unknown",
                "ascii_map": world.render_ascii_map(),
            }
        except FileNotFoundError:
            return {}
        except Exception:
            return {}
