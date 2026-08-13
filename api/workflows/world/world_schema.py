

from contextvars import ContextVar
from pathlib import Path
from typing import Any, Literal, Dict, List
import os

from pydantic import BaseModel, Field


class WorldContext:
    """Holds the current world instance and its file path for the active workflow turn."""

    def __init__(self) -> None:
        self._world: ContextVar["World | None"] = ContextVar(
            "world_context_world", default=None
        )
        self._world_path: ContextVar[Path | None] = ContextVar(
            "world_context_path", default=None
        )

    def set_world(self, world: "World", path: str | Path | None = None) -> None:
        self._world.set(world)
        if path is not None:
            self._world_path.set(Path(path))

    def get_world(self) -> "World | None":
        return self._world.get()

    def get_world_path(self) -> Path | None:
        return self._world_path.get()


world_context = WorldContext()


def set_current_world(world: "World", path: str | Path | None = None) -> None:
    world_context.set_world(world, path=path)


def get_current_world() -> "World | None":
    return world_context.get_world()


def get_current_world_path() -> Path | None:
    return world_context.get_world_path()


class Item(BaseModel):
    """Represents an object the player can interact with."""
    id: str = Field(description="A unique, stable identifier for this item.")
    name: str = Field(description="The common name of the item.")
    description: str = Field(description="Detailed description for when the player examines it.")
    is_collectible: bool = Field(default=True, description="Can the player pick it up?")


class Exit(BaseModel):
    destination_id: str | None = Field(default=None, description="ID of the destination location, or None if this direction is known to be passable but not yet explored/generated.")
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

    # Item IDs currently present in this room.
    item_ids: List[str] = Field(default_factory=list, description="Item IDs present in the location.")


class Character(BaseModel):
    """
    Represents a character in the world, which could be the player or NPCs.
    """
    id: str = Field(description="A unique, stable identifier for this character.")
    name: str = Field(description="The character's name.")
    description: str = Field(description="A description of the character.")
    notes: str = Field(default="", description="Notes about the characters personality and background.")
    inventory_ids: List[str] = Field(default_factory=list, description="Item IDs the character is carrying.")
    location_id: str = Field(description="The unique ID of the location where the character currently is.")


class World(BaseModel):
    """
    The root object that holds the entire world.
    """
    # Dictionary mapping Location ID to the full Location object. 
    # Using IDs allows for easy, stable referencing.
    description: str = Field(description="A brief description of the world, for context.")
    locations: Dict[str, Location] = Field(description="All locations in the map, keyed by their unique ID.")
    event_log: List[str] = Field(default_factory=list, description="Chronological summary of significant past events, for grounding LLM context.")

    # Tracks where the player currently is. This MUST match one of the Location IDs.
    player: Character = Field(description="The player character and their current state.")
    characters: dict[str, Character] = Field(default_factory=dict, description="Non-player characters in the world, keyed by their unique ID.")
    items: Dict[str, Item] = Field(default_factory=dict, description="All items in the world keyed by item ID.")

    def get_item(self, item_id: str) -> Item | None:
        return self.items.get(item_id)

    def has_item(self, item_id: str) -> bool:
        return item_id in self.items

    def get_location_items(self, location_id: str) -> list[Item]:
        location = self.locations.get(location_id)
        if location is None:
            return []
        return [self.items[item_id] for item_id in location.item_ids if item_id in self.items]

    def get_character_items(self, character_id: str) -> list[Item]:
        character = self.player if character_id == self.player.id else self.characters.get(character_id)
        if character is None:
            return []
        return [self.items[item_id] for item_id in character.inventory_ids if item_id in self.items]

    def add_item(self, item: Item) -> None:
        if item.id in self.items:
            raise ValueError(f"Item '{item.id}' already exists in the world")
        self.items[item.id] = item

    def add_item_to_location(self, item: Item, location_id: str) -> None:
        if item.id in self.items:
            raise ValueError(f"Item '{item.id}' already exists in the world")
        location = self.locations.get(location_id)
        if location is None:
            raise ValueError(f"Location '{location_id}' does not exist")
        self.items[item.id] = item
        location.item_ids.append(item.id)

    def add_item_to_character(self, item: Item, character_id: str) -> None:
        if item.id in self.items:
            raise ValueError(f"Item '{item.id}' already exists in the world")
        character = self.player if character_id == self.player.id else self.characters.get(character_id)
        if character is None:
            raise ValueError(f"Character '{character_id}' does not exist")
        self.items[item.id] = item
        character.inventory_ids.append(item.id)

    def _remove_item_references(self, item_id: str) -> None:
        for location in self.locations.values():
            if item_id in location.item_ids:
                location.item_ids.remove(item_id)
        if item_id in self.player.inventory_ids:
            self.player.inventory_ids.remove(item_id)
        for character in self.characters.values():
            if item_id in character.inventory_ids:
                character.inventory_ids.remove(item_id)

    def move_item_to_location(self, item_id: str, location_id: str) -> None:
        if item_id not in self.items:
            raise ValueError(f"Item '{item_id}' does not exist in the world")
        location = self.locations.get(location_id)
        if location is None:
            raise ValueError(f"Location '{location_id}' does not exist")
        self._remove_item_references(item_id)
        location.item_ids.append(item_id)

    def move_item_to_character(self, item_id: str, character_id: str) -> None:
        if item_id not in self.items:
            raise ValueError(f"Item '{item_id}' does not exist in the world")
        character = self.player if character_id == self.player.id else self.characters.get(character_id)
        if character is None:
            raise ValueError(f"Character '{character_id}' does not exist")
        self._remove_item_references(item_id)
        character.inventory_ids.append(item_id)

    def remove_item(self, item_id: str) -> None:
        if item_id not in self.items:
            raise ValueError(f"Item '{item_id}' does not exist in the world")
        self._remove_item_references(item_id)
        del self.items[item_id]

    def get_character(self, character_id: str) -> Character | None:
        if character_id == self.player.id:
            return self.player
        return self.characters.get(character_id)

    def save_to_file(self, file_path: str | Path) -> Path:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        tmp = path.with_suffix(f"{path.suffix}.tmp")
        tmp.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return path

    @classmethod
    def load_from_file(cls, file_path: str | Path) -> "World":
        path = Path(file_path)
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def render_ascii_map(self) -> str:
        """Render an ASCII map of the world's locations using box-drawing characters.

        Each footprint coordinate becomes a 1x1 cell with walls of 1 character width
        surrounding it. Walls are shared between adjacent cells.
        """
        coords: dict[tuple[int, int], str] = {}
        for loc_id, loc in self.locations.items():
            for x, y in loc.footprint:
                coords[(x, y)] = loc_id

        if not coords:
            return "(empty world)"

        xs = [p[0] for p in coords]
        ys = [p[1] for p in coords]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)

        width = max_x - min_x + 1
        height = max_y - min_y + 1

        cg_h = height * 2 + 1
        cg_w = width * 2 + 1

        grid = [[" " for _ in range(cg_w)] for _ in range(cg_h)]

        def set_h(r, c):
            if 0 <= r < cg_h and 0 <= c < cg_w:
                grid[r][c] = "─"

        def set_v(r, c):
            if 0 <= r < cg_h and 0 <= c < cg_w:
                grid[r][c] = "│"

        for (x, y), loc_id in coords.items():
            cx = x - min_x
            cy = y - min_y
            ir = cy * 2 + 1
            ic = cx * 2 + 1
            grid[ir][ic] = (loc_id[0] if loc_id else ".")

            north = coords.get((x, y - 1))
            south = coords.get((x, y + 1))
            west = coords.get((x - 1, y))
            east = coords.get((x + 1, y))

            if not north:
                set_h(ir - 1, ic)
            if not south:
                set_h(ir + 1, ic)
            if not west:
                set_v(ir, ic - 1)
            if not east:
                set_v(ir, ic + 1)

        # determine corner/intersection chars
        for r in range(0, cg_h, 2):
            for c in range(0, cg_w, 2):
                N = (r - 1 >= 0) and grid[r - 1][c] != " "
                E = (c + 1 < cg_w) and grid[r][c + 1] != " "
                S = (r + 1 < cg_h) and grid[r + 1][c] != " "
                W = (c - 1 >= 0) and grid[r][c - 1] != " "
                if N and E and S and W:
                    ch = "┼"
                elif N and S and (not E and not W):
                    ch = "│"
                elif E and W and (not N and not S):
                    ch = "─"
                elif N and E and S:
                    ch = "├"
                elif E and S and W:
                    ch = "┬"
                elif S and W and N:
                    ch = "┤"
                elif W and N and E:
                    ch = "┴"
                elif S and E:
                    ch = "┌"
                elif S and W:
                    ch = "┐"
                elif N and E:
                    ch = "└"
                elif N and W:
                    ch = "┘"
                elif N or S:
                    ch = "│"
                elif E or W:
                    ch = "─"
                else:
                    ch = " "
                grid[r][c] = ch

        return "\n".join("".join(row) for row in grid)


