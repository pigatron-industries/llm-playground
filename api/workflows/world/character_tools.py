"""Character-related tools for the world explorer workflow."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ...tools.registry import register_tool
from .location_tools import ItemArgs
from .world_schema import (
    Character,
    Item,
    World,
    get_current_world,
    get_current_world_path,
)


class CreateCharacterArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    character_id: str = Field(description="A unique ID for the new character.")
    name: str = Field(description="The display name for the new character.")
    description: str = Field(description="Narrative description of the character.")
    notes: str = Field(
        default="",
        description="Notes about the character's personality and background.",
    )
    location_id: str = Field(description="The ID of the location where the character starts.")
    items: list[ItemArgs] = Field(
        default_factory=list,
        description="Items to create and give the character on creation.",
    )


@register_tool(CreateCharacterArgs, description="Create a new character (NPC) in the current world.", category="World")
def create_character(
    character_id: str,
    name: str,
    description: str,
    notes: str = "",
    location_id: str | None = None,
    items: list[ItemArgs] | None = None,
) -> str:
    world = get_current_world()
    if world is None:
        return "Error: no world is loaded for this workflow"

    if character_id == world.player.id:
        return f"Error: cannot create a character with the player's ID '{character_id}'"

    if character_id in world.characters:
        return f"Error: character '{character_id}' already exists"

    if location_id is None or location_id not in world.locations:
        return f"Error: location '{location_id}' does not exist"

    character = Character(
        id=character_id,
        name=name,
        description=description,
        notes=notes,
        location_id=location_id,
    )
    world.characters[character_id] = character

    # Create items and add to character's inventory
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
            world.add_item_to_character(item, character_id)
    except ValueError as exc:
        del world.characters[character_id]
        return f"Error: {exc}"
    except Exception as exc:
        del world.characters[character_id]
        return f"Error: invalid item data: {exc}"

    world_path = get_current_world_path()
    if world_path is not None:
        try:
            world.save_to_file(world_path)
        except Exception as exc:
            return f"Created character '{character_id}', but failed to save world: {exc}"

    return f"Created character '{character_id}' ({name}) at location '{location_id}'."


class UpdateCharacterArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    character_id: str = Field(description="The ID of the character to update.")
    name: str | None = Field(
        default=None,
        description="New display name for the character. Omit to keep the existing name.",
    )
    description: str | None = Field(
        default=None,
        description="New narrative description for the character. Omit to keep the existing description.",
    )
    notes: str | None = Field(
        default=None,
        description="New notes about the character's personality and background. Omit to keep current notes.",
    )
    location_id: str | None = Field(
        default=None,
        description="New location for the character. Omit to keep the current location.",
    )


@register_tool(UpdateCharacterArgs, description="Update a character's name, description, notes, or location.", category="World")
def update_character(
    character_id: str,
    name: str | None = None,
    description: str | None = None,
    notes: str | None = None,
    location_id: str | None = None,
) -> str:
    world = get_current_world()
    if world is None:
        return "Error: no world is loaded for this workflow"

    character = world.get_character(character_id)
    if character is None:
        return f"Error: character '{character_id}' does not exist"

    if name is not None:
        character.name = name
    if description is not None:
        character.description = description
    if notes is not None:
        character.notes = notes
    if location_id is not None:
        if location_id not in world.locations:
            return f"Error: location '{location_id}' does not exist"
        character.location_id = location_id

    world_path = get_current_world_path()
    if world_path is not None:
        try:
            world.save_to_file(world_path)
        except Exception as exc:
            return f"Updated character '{character_id}', but failed to save world: {exc}"

    return f"Updated character '{character_id}'."


class RemoveCharacterArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    character_id: str = Field(description="The ID of the character to remove.")
    return_items_to_location: str | None = Field(
        default=None,
        description="Location ID to return the character's items to. If omitted, items are deleted with the character.",
    )


@register_tool(RemoveCharacterArgs, description="Remove a character from the world, optionally returning their items to a location.", category="World")
def remove_character(
    character_id: str,
    return_items_to_location: str | None = None,
) -> str:
    world = get_current_world()
    if world is None:
        return "Error: no world is loaded for this workflow"

    if character_id == world.player.id:
        return f"Error: cannot remove the player character"

    character = world.characters.get(character_id)
    if character is None:
        return f"Error: character '{character_id}' does not exist"

    # Handle inventory items
    if return_items_to_location is not None:
        if return_items_to_location not in world.locations:
            return f"Error: location '{return_items_to_location}' does not exist"
        for item_id in list(character.inventory_ids):
            try:
                world.move_item_to_location(item_id, return_items_to_location)
            except ValueError:
                return f"Error: failed to move item '{item_id}' to location '{return_items_to_location}'"

    del world.characters[character_id]

    world_path = get_current_world_path()
    if world_path is not None:
        try:
            world.save_to_file(world_path)
        except Exception as exc:
            return f"Removed character '{character_id}', but failed to save world: {exc}"

    return f"Removed character '{character_id}'."
