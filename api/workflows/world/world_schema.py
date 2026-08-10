

from typing import Any, Literal, Dict, List

from pydantic import BaseModel, Field


class Item(BaseModel):
    """Represents an object the player can interact with."""
    id: str = Field(description="A unique, stable identifier for this item.")
    name: str = Field(description="The common name of the item.")
    description: str = Field(description="Detailed description for when the player examines it.")
    is_collectible: bool = Field(default=True, description="Can the player pick it up?")


class Exit(BaseModel):
    destination_id: str
    locked: bool = False
    description: str = ""  # "a heavy iron door"


class Location(BaseModel):
    """
    A single node in the game world. This holds all the facts about a place at any given time.
    """
    id: str = Field(description="A unique, stable identifier for this location.")
    footprint: list[tuple[int, int]] = Field(description="Grid cells this location visually occupies.")
    name: str = Field(description="The common, short name of the location.")

    # The narrative description that is shown to the player. This should be rich prose.
    description: str = Field(description="The main narrative description of the place.")
    state: dict[str, Any] = Field(default_factory=dict, description="Structured, queryable facts about this location's current state, e.g. {'door_locked': True}.")

    # Links to other locations using their IDs. Keys are directions.
    # e.g., {"north": "id_of_next_location"}
    exits: dict[Literal['north','south','east','west'], Exit] = Field(
        default_factory=dict, 
        description="Directions and the Exit object for each exit."
    )

    # Objects currently present in this room.
    items: List[Item] = Field(default_factory=list, description="Objects present in the location.")



class Character(BaseModel):
    """
    Represents a character in the world, which could be the player or NPCs.
    """
    id: str = Field(description="A unique, stable identifier for this character.")
    name: str = Field(description="The character's name.")
    description: str = Field(description="A description of the character.")
    notes: str = Field(default="", description="Notes about the characters personality and background.")
    inventory: List[Item] = Field(default_factory=list, description="Items the character is carrying.")
    location_id: str = Field(description="The unique ID of the location where the character currently is.")


class World(BaseModel):
    """
    The root object that holds the entire world.
    """
    # Dictionary mapping Location ID to the full Location object. 
    # Using IDs allows for easy, stable referencing.
    locations: Dict[str, Location] = Field(description="All locations in the map, keyed by their unique ID.")
    event_log: List[str] = Field(default_factory=list, description="Chronological summary of significant past events, for grounding LLM context.")

    # Tracks where the player currently is. This MUST match one of the Location IDs.
    player: Character = Field(description="The player character and their current state.")
    characters: dict[str, Character] = Field(default_factory=dict, description="Non-player characters in the world, keyed by their unique ID.")


