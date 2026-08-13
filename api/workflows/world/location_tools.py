"""Location-related tools for the world explorer workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ...tools.registry import register_tool
from .world_schema import (
    Exit,
    Item,
    Location,
    World,
    get_current_world,
    get_current_world_path,
)


class ItemArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str = Field(description="A unique ID for the item.")
    name: str = Field(description="The display name of the item.")
    description: str = Field(description="A description of the item.")
    is_collectible: bool = Field(default=True, description="Whether the item can be picked up.")


class ExitArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destination_id: str | None = Field(
        default=None,
        description="ID of the destination location, or None if this direction is known to be passable but not yet explored/generated.",
    )
    locked: bool = Field(default=False, description="Whether this exit is locked.")
    description: str = Field(default="", description="A description of the exit.")


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

    item_names = [item.name for item in world.get_location_items(location_id)]
    return (
        f"Location {location_id}: {location.name}\n"
        f"Description: {location.description}\n"
        f"State: {state}\n"
        f"Items: {item_names}\n"
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
    exits: dict[str, ExitArgs] | None = None,
    items: list[ItemArgs] | None = None,
    state: dict[str, Any] | None = None,
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
            for direction, exit_data in (exits or {}).items()
        }
    except Exception as exc:
        return f"Error: invalid exits data: {exc}"

    location = Location(
        id=location_id,
        footprint=footprint_tuples,
        name=name,
        description=description,
        state=state or {},
        exits=location_exits,
        item_ids=[],
    )
    world.locations[location_id] = location

    try:
        for item_data in (items or []):
            if hasattr(item_data, "model_dump"):
                data = item_data.model_dump()
            elif isinstance(item_data, dict):
                data = item_data
            else:
                raise ValueError("Invalid item entry; expected dict or ItemArgs")

            item_id = data.get("item_id")
            if not item_id:
                raise ValueError("Missing 'item_id' for item")

            if item_id in world.items:
                raise ValueError(f"Item '{item_id}' already exists in the world")

            item = Item(
                id=item_id,
                name=data.get("name", ""),
                description=data.get("description", ""),
                is_collectible=data.get("is_collectible", True),
            )
            world.add_item_to_location(item, location_id)
    except ValueError as exc:
        del world.locations[location_id]
        return f"Error: {exc}"
    except Exception as exc:
        del world.locations[location_id]
        return f"Error: invalid item data: {exc}"

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


@register_tool(UpdateLocationArgs, description="Update the description and/or state of an existing location. Update when new facts become known about the location.", category="World")
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


class UpdateExitArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location_id: str = Field(description="The ID of the location containing the exit.")
    direction: Literal["north", "south", "east", "west"] = Field(
        description="Which direction's exit to update."
    )
    destination_id: str | None = Field(
        default=None,
        description="New destination location ID. Omit to keep current destination.",
    )
    locked: bool | None = Field(
        default=None,
        description="New locked state. Omit to keep current value.",
    )
    description: str | None = Field(
        default=None,
        description="New description for the exit. Omit to keep current description.",
    )


@register_tool(UpdateExitArgs, description="Update an exit's destination, locked state, or description.", category="World")
def update_exit(
    location_id: str,
    direction: Literal["north", "south", "east", "west"],
    destination_id: str | None = None,
    locked: bool | None = None,
    description: str | None = None,
) -> str:
    world = get_current_world()
    if world is None:
        return "Error: no world is loaded for this workflow"

    location = world.locations.get(location_id)
    if location is None:
        return f"Error: location '{location_id}' does not exist"

    if direction not in location.exits:
        return f"Error: location '{location_id}' has no exit in direction '{direction}'"

    exit_obj = location.exits[direction]

    if destination_id is not None:
        if destination_id not in world.locations:
            return f"Error: destination location '{destination_id}' does not exist"
        exit_obj.destination_id = destination_id

    if locked is not None:
        exit_obj.locked = locked

    if description is not None:
        exit_obj.description = description

    world_path = get_current_world_path()
    if world_path is not None:
        try:
            world.save_to_file(world_path)
        except Exception as exc:
            return f"Updated exit on '{location_id}' but failed to save world: {exc}"

    return f"Updated exit '{direction}' on location '{location_id}'."
