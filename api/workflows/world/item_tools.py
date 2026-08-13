"""Item-related tools for the world explorer workflow."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ...tools.registry import register_tool
from .world_schema import (
    Item,
    World,
    get_current_world,
    get_current_world_path,
)


class UpdateItemArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str = Field(description="The ID of the item to update.")
    name: str | None = Field(
        default=None,
        description="New display name for the item. Omit to keep the existing name.",
    )
    description: str | None = Field(
        default=None,
        description="New description for the item. Omit to keep the existing description.",
    )
    is_collectible: bool | None = Field(
        default=None,
        description="New collectible flag. Omit to keep the current value.",
    )


@register_tool(UpdateItemArgs, description="Update the name, description, or collectible flag of an existing item anywhere in the world.", category="World")
def update_item(
    item_id: str,
    name: str | None = None,
    description: str | None = None,
    is_collectible: bool | None = None,
) -> str:
    world = get_current_world()
    if world is None:
        return "Error: no world is loaded for this workflow"

    item = world.items.get(item_id)
    if item is None:
        return f"Error: item '{item_id}' not found in the world"

    if name is not None:
        item.name = name
    if description is not None:
        item.description = description
    if is_collectible is not None:
        item.is_collectible = is_collectible

    world_path = get_current_world_path()
    if world_path is not None:
        try:
            world.save_to_file(world_path)
        except Exception as exc:
            return f"Updated item '{item_id}', but failed to save world: {exc}"

    return f"Updated item '{item_id}'."


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

    try:
        item = Item(
            id=item_id,
            name=name,
            description=description,
            is_collectible=is_collectible,
        )
        world.add_item_to_location(item, location_id)
    except ValueError as exc:
        return f"Error: {exc}"

    world_path = get_current_world_path()
    if world_path is not None:
        try:
            world.save_to_file(world_path)
        except Exception as exc:
            return f"Added item '{item_id}' to location '{location_id}', but failed to save world: {exc}"

    return f"Added item '{item_id}' to location '{location_id}'."


class AddItemToCharacterArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    character_id: str = Field(description="The ID of the character to receive the item.")
    item_id: str = Field(description="A unique ID for the new item.")
    name: str = Field(description="The display name of the item.")
    description: str = Field(description="A description of the item.")
    is_collectible: bool = Field(default=True, description="Whether the item can be picked up.")


@register_tool(AddItemToCharacterArgs, description="Create a new item and add it directly to a character's inventory.", category="World")
def add_item_to_character(
    character_id: str,
    item_id: str,
    name: str,
    description: str,
    is_collectible: bool = True,
) -> str:
    world = get_current_world()
    if world is None:
        return "Error: no world is loaded for this workflow"

    if character_id != world.player.id and character_id not in world.characters:
        return f"Error: character '{character_id}' does not exist"

    try:
        item = Item(
            id=item_id,
            name=name,
            description=description,
            is_collectible=is_collectible,
        )
        world.add_item_to_character(item, character_id)
    except ValueError as exc:
        return f"Error: {exc}"

    world_path = get_current_world_path()
    if world_path is not None:
        try:
            world.save_to_file(world_path)
        except Exception as exc:
            return f"Added item '{item_id}' to character '{character_id}', but failed to save world: {exc}"

    return f"Added item '{item_id}' to character '{character_id}'."


class MoveItemToCharacterArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location_id: str = Field(description="The ID of the location to take the item from.")
    character_id: str = Field(description="The ID of the character to receive the item.")
    item_id: str = Field(description="The ID of the item to move.")


@register_tool(MoveItemToCharacterArgs, description="Move an item from a location into a character's inventory.", category="World")
def move_item_to_character(location_id: str, character_id: str, item_id: str) -> str:
    world = get_current_world()
    if world is None:
        return "Error: no world is loaded for this workflow"

    location = world.locations.get(location_id)
    if location is None:
        return f"Error: location '{location_id}' does not exist"

    if character_id != world.player.id and character_id not in world.characters:
        return f"Error: character '{character_id}' does not exist"

    if item_id not in world.items:
        return f"Error: item '{item_id}' does not exist in the world"
    if item_id not in location.item_ids:
        return f"Error: item '{item_id}' is not present in location '{location_id}'"

    try:
        world.move_item_to_character(item_id, character_id)
    except ValueError as exc:
        return f"Error: {exc}"

    world_path = get_current_world_path()
    if world_path is not None:
        try:
            world.save_to_file(world_path)
        except Exception as exc:
            return f"Moved item '{item_id}' to '{character_id}', but failed to save world: {exc}"

    return f"Moved item '{item_id}' from '{location_id}' to character '{character_id}'."


class MoveItemToLocationArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str = Field(description="The ID of the item to move.")
    location_id: str = Field(description="The ID of the destination location.")


@register_tool(MoveItemToLocationArgs, description="Move an item into a location.", category="World")
def move_item_to_location(item_id: str, location_id: str) -> str:
    world = get_current_world()
    if world is None:
        return "Error: no world is loaded for this workflow"

    location = world.locations.get(location_id)
    if location is None:
        return f"Error: location '{location_id}' does not exist"

    if item_id not in world.items:
        return f"Error: item '{item_id}' does not exist in the world"

    try:
        world.move_item_to_location(item_id, location_id)
    except ValueError as exc:
        return f"Error: {exc}"

    world_path = get_current_world_path()
    if world_path is not None:
        try:
            world.save_to_file(world_path)
        except Exception as exc:
            return f"Moved item '{item_id}' to '{location_id}', but failed to save world: {exc}"

    return f"Moved item '{item_id}' to location '{location_id}'."
