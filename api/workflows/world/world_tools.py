"""World-specific tools for the world explorer workflow."""

from __future__ import annotations

import contextvars
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ...tools.registry import register_tool
from .world_schema import Exit, Item, Location, World

# TODO:
# update_item, remove_item, move_item_to_character, move_item_to_location,
# update_exit, remove_exit_from_location,


_current_world: contextvars.ContextVar[World | None] = contextvars.ContextVar(
    "current_world",
    default=None,
)

_current_world_path: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "current_world_path",
    default=None,
)


def set_current_world(world: World, path: str | Path | None = None) -> None:
    _current_world.set(world)
    if path is not None:
        _current_world_path.set(Path(path))


def get_current_world() -> World | None:
    return _current_world.get()


def get_current_world_path() -> Path | None:
    return _current_world_path.get()


class InspectLocationArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location_id: str = Field(description="The ID of the location to inspect.")

@register_tool(InspectLocationArgs, description="Inspect a specific location in the world.", category="World")
def inspect_location(location_id: str) -> str:
    world = get_current_world()
    if world is None:
        return "Error: no world is loaded for this workflow"

    location = world.locations.get(location_id)
    if location is None:
        return f"Error: location '{location_id}' does not exist"

    state = location.state or {}
    exits = {
        direction: {
            "destination_id": exit.destination_id,
            "locked": exit.locked,
            "description": exit.description,
        }
        for direction, exit in location.exits.items()
    }

    return (
        f"Location {location_id}: {location.name}\n"
        f"Description: {location.description}\n"
        f"State: {state}\n"
        f"Items: {[item.name for item in location.items]}\n"
        f"Exits: {exits}"
    )


class ListLocationsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

@register_tool(ListLocationsArgs, description="List known locations in the world.", category="World")
def list_locations() -> str:
    world = get_current_world()
    if world is None:
        return "Error: no world is loaded for this workflow"

    lines = [f"Locations in world '{world.description}':"]
    for location_id, location in world.locations.items():
        lines.append(f"- {location_id}: {location.name}")
    return "\n".join(lines)



class ItemArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str = Field(description="A unique ID for the item.")
    name: str = Field(description="The display name of the item.")
    description: str = Field(description="A description of the item.")
    is_collectible: bool = Field(default=True, description="Whether the item can be picked up.")

class ExitArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destination_id: str = Field(description="The ID of the destination location.")
    locked: bool = Field(default=False, description="Whether this exit is locked.")
    description: str = Field(default="", description="A description of the exit.")

class CreateLocationArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location_id: str = Field(description="A unique ID for the new location.")
    name: str = Field(description="The display name for the new location.")
    description: str = Field(description="Narrative description of the new location.")
    footprint: list[list[int]] = Field(
        default_factory=lambda: [[0, 0]],
        description="Grid coordinates occupied by this location, e.g. [[0,0], [0,1]].",
    )
    exits: dict[str, ExitArgs] = Field(
        default_factory=dict,
        description="Optional exits keyed by direction. Each value should include destination_id, locked, and description.",
    )
    items: list[ItemArgs] = Field(
        default_factory=list,
        description="Optional items to place in the new location.",
    )
    state: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional structured state for the new location.",
    )

@register_tool(CreateLocationArgs, description="Create a new location in the current world.", category="World")
def create_location(
    location_id: str,
    name: str,
    description: str,
    footprint: list[list[int]] = [[0, 0]],
    exits: dict[str, ExitArgs] = {},
    items: list[ItemArgs] | None = None,
    state: dict[str, Any] = {},
) -> str:
    world = get_current_world()
    if world is None:
        return "Error: no world is loaded for this workflow"

    if location_id in world.locations:
        return f"Error: location '{location_id}' already exists"

    try:
        footprint_tuples = [tuple(coord) for coord in footprint]
    except Exception:
        return "Error: invalid footprint format; expected a list of [x, y] coordinates"

    existing_footprint_cells = {
        cell
        for location in world.locations.values()
        for cell in location.footprint
    }
    overlap = set(footprint_tuples) & existing_footprint_cells
    if overlap:
        overlap_list = ", ".join(str(cell) for cell in sorted(overlap))
        return f"Error: footprint overlaps an existing location at {overlap_list}"

    try:
        location_exits = {
            direction: Exit(**exit_data.model_dump()) if hasattr(exit_data, "model_dump") else Exit(**exit_data)
            for direction, exit_data in exits.items()
        }
    except Exception as exc:
        return f"Error: invalid exits data: {exc}"

    try:
        location_items = [
            Item(
                id=item_data.item_id,
                name=item_data.name,
                description=item_data.description,
                is_collectible=item_data.is_collectible,
            )
            for item_data in (items or [])
        ]
    except Exception as exc:
        return f"Error: invalid item data: {exc}"

    location = Location(
        id=location_id,
        footprint=footprint_tuples,
        name=name,
        description=description,
        state=state,
        exits=location_exits,
        items=location_items,
    )

    world.locations[location_id] = location

    world_path = get_current_world_path()
    if world_path is not None:
        try:
            world.save_to_file(world_path)
        except Exception as exc:
            return f"Created location '{location_id}', but failed to save world: {exc}"

    return f"Created location '{location_id}' in the current world."


class UpdateLocationArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location_id: str = Field(description="The ID of the location to update.")
    description: str | None = Field(
        default=None,
        description="New narrative description for the location. Omit to keep the existing description.",
    )
    state: dict[str, Any] | None = Field(
        default=None,
        description="New structured state for the location. Replaces the entire existing state. Omit to keep the current state.",
    )

@register_tool(UpdateLocationArgs, description="Update the description and/or state of an existing location.", category="World")
def update_location(
    location_id: str,
    description: str | None = None,
    state: dict[str, Any] | None = None,
) -> str:
    world = get_current_world()
    if world is None:
        return "Error: no world is loaded for this workflow"

    location = world.locations.get(location_id)
    if location is None:
        return f"Error: location '{location_id}' does not exist"

    if description is not None:
        location.description = description
    if state is not None:
        location.state = state

    world_path = get_current_world_path()
    if world_path is not None:
        try:
            world.save_to_file(world_path)
        except Exception as exc:
            return f"Updated location '{location_id}', but failed to save world: {exc}"

    return f"Updated location '{location_id}'."


class AddExitArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location_id: str = Field(description="The ID of the location to add the exit to.")
    direction: Literal["north", "south", "east", "west"] = Field(
        description="The direction of the new exit."
    )
    destination_id: str = Field(description="The ID of the destination location.")
    description: str = Field(default="", description="A description of the exit.")
    locked: bool = Field(default=False, description="Whether this exit is locked.")

@register_tool(AddExitArgs, description="Add an exit to an existing location.", category="World")
def add_exit_to_location(
    location_id: str,
    direction: Literal["north", "south", "east", "west"],
    destination_id: str,
    description: str = "",
    locked: bool = False,
) -> str:
    world = get_current_world()
    if world is None:
        return "Error: no world is loaded for this workflow"

    location = world.locations.get(location_id)
    if location is None:
        return f"Error: location '{location_id}' does not exist"

    if direction in location.exits:
        return f"Error: location '{location_id}' already has an exit to the {direction}"

    if destination_id not in world.locations:
        return f"Error: destination location '{destination_id}' does not exist"

    location.exits[direction] = Exit(
        destination_id=destination_id,
        locked=locked,
        description=description,
    )

    world_path = get_current_world_path()
    if world_path is not None:
        try:
            world.save_to_file(world_path)
        except Exception as exc:
            return f"Added exit to '{direction}' on '{location_id}', but failed to save world: {exc}"

    return f"Added exit '{direction}' from '{location_id}' to '{destination_id}'"


class AddItemArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location_id: str = Field(description="The ID of the location to add the item to.")
    item_id: str = Field(description="A unique ID for the new item.")
    name: str = Field(description="The display name of the item.")
    description: str = Field(description="A description of the item.")
    is_collectible: bool = Field(default=True, description="Whether the item can be picked up.")

@register_tool(AddItemArgs, description="Add a new item to an existing location.", category="World")
def add_item_to_location(
    location_id: str,
    item_id: str,
    name: str,
    description: str,
    is_collectible: bool = True,
) -> str:
    world = get_current_world()
    if world is None:
        return "Error: no world is loaded for this workflow"

    location = world.locations.get(location_id)
    if location is None:
        return f"Error: location '{location_id}' does not exist"

    if any(item.id == item_id for item in location.items):
        return f"Error: item '{item_id}' already exists in location '{location_id}'"

    item = Item(
        id=item_id,
        name=name,
        description=description,
        is_collectible=is_collectible,
    )
    location.items.append(item)

    world_path = get_current_world_path()
    if world_path is not None:
        try:
            world.save_to_file(world_path)
        except Exception as exc:
            return f"Added item '{item_id}' to location '{location_id}', but failed to save world: {exc}"

    return f"Added item '{item_id}' to location '{location_id}'."