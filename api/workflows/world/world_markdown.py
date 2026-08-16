"""Folder-based markdown (de)serialization for ``World`` data.

A world is stored as a folder so its contents are easy to read and edit:

    <root>/
    ├── world.md                     # description, event log, player id
    ├── locations/<id>.md           # one file per Location
    ├── characters/<id>.md          # one file per Character (player + NPCs)
    └── items/<id>.md               # one file per Item

Each entity file uses YAML frontmatter for structured/relational fields, an H1
heading for the entity's ``name``, and a markdown body for its ``description``.
The filename (without extension) is the entity id.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .world_schema import Character, Exit, Item, Location, World


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Split a document into ``(frontmatter, body)``.

    Frontmatter is a leading ``---\\n ... \\n---\\n`` YAML block. When absent,
    returns an empty dict and the full text.
    """
    text = text.replace("\r\n", "\n")
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            raw = text[4:end]
            body = text[end + len("\n---\n"):]
            data = yaml.safe_load(raw)
            if data is None:
                data = {}
            if not isinstance(data, dict):
                raise ValueError("frontmatter must be a YAML mapping")
            return data, body
    return {}, text


def _dump_frontmatter(data: dict) -> str:
    return yaml.safe_dump(
        data, sort_keys=False, default_flow_style=False, allow_unicode=True
    ).rstrip("\n")


def _parse_name_and_description(body: str) -> tuple[str, str]:
    """Split a markdown body into ``(name, description)``.

    The name is the first H1 (``# ...``) line; the description is the remaining
    text. If the first non-empty line is not a heading, the name is empty and
    the whole body is treated as the description.
    """
    lines = body.split("\n")
    i = 0
    while i < len(lines) and lines[i].strip() == "":
        i += 1
    if i < len(lines) and lines[i].startswith("# "):
        name = lines[i][2:].strip()
        i += 1
    else:
        name = ""
    description = "\n".join(lines[i:]).strip()
    return name, description


def _write_atomically(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _render_entity(frontmatter: dict, name: str, description: str) -> str:
    parts: list[str] = []
    if frontmatter:
        parts.append("---")
        parts.append(_dump_frontmatter(frontmatter))
        parts.append("---")
    parts.append(f"# {name}")
    if description:
        parts.append("")
        parts.append(description.rstrip("\n"))
    return "\n".join(parts) + "\n"


def _write_entity_folder(folder: Path, contents: dict[str, str]) -> None:
    """Write every ``<id>.md`` and remove files whose id is no longer present."""
    folder.mkdir(parents=True, exist_ok=True)
    for entity_id, text in contents.items():
        _write_atomically(folder / f"{entity_id}.md", text)
    for stale in folder.glob("*.md"):
        if stale.stem not in contents:
            stale.unlink()


# --- per-entity frontmatter ---


def _location_fm(loc: Location) -> dict:
    return {
        "footprint": [list(point) for point in loc.footprint],
        "state": dict(loc.state),
        "exits": {
            direction: {
                "destination_id": exit_.destination_id,
                "locked": exit_.locked,
                "description": exit_.description,
            }
            for direction, exit_ in loc.exits.items()
        },
        "item_ids": list(loc.item_ids),
    }


def _character_fm(character: Character) -> dict:
    return {
        "location_id": character.location_id,
        "inventory_ids": list(character.inventory_ids),
        "notes": character.notes,
    }


def _item_fm(item: Item) -> dict:
    return {"is_collectible": item.is_collectible}


def _parse_location(stem: str, fm: dict, name: str, description: str) -> Location:
    exits: dict[str, Exit] = {}
    for direction, data in (fm.get("exits") or {}).items():
        exits[direction] = Exit(
            destination_id=data.get("destination_id"),
            locked=bool(data.get("locked", False)),
            description=data.get("description", ""),
        )
    return Location(
        id=stem,
        name=name,
        footprint=[tuple(point) for point in (fm.get("footprint") or [])],
        description=description,
        state=dict(fm.get("state") or {}),
        exits=exits,
        item_ids=list(fm.get("item_ids") or []),
    )


def _parse_character(stem: str, fm: dict, name: str, description: str) -> Character:
    return Character(
        id=stem,
        name=name,
        description=description,
        notes=fm.get("notes", "") or "",
        location_id=fm.get("location_id", "") or "",
        inventory_ids=list(fm.get("inventory_ids") or []),
    )


def _parse_item(stem: str, fm: dict, name: str, description: str) -> Item:
    return Item(
        id=stem,
        name=name,
        description=description,
        is_collectible=bool(fm.get("is_collectible", True)),
    )


def _render_world(world: World) -> str:
    fm = {
        "event_log": list(world.event_log),
        "player_id": world.player.id,
    }
    parts = ["---", _dump_frontmatter(fm), "---"]
    if world.description:
        parts.append("")
        parts.append(world.description.rstrip("\n"))
    return "\n".join(parts) + "\n"


def _read_entity_folder(
    folder: Path, parser
) -> dict:
    results: dict = {}
    for path in sorted(folder.glob("*.md")):
        fm, body = _split_frontmatter(path.read_text(encoding="utf-8"))
        name, description = _parse_name_and_description(body)
        results[path.stem] = parser(path.stem, fm, name, description)
    return results


def save_world(world: World, root: Path) -> Path:
    root = Path(root)
    _write_atomically(root / "world.md", _render_world(world))
    _write_entity_folder(
        root / "locations",
        {
            loc_id: _render_entity(_location_fm(loc), loc.name, loc.description)
            for loc_id, loc in world.locations.items()
        },
    )
    characters = {world.player.id: world.player, **world.characters}
    _write_entity_folder(
        root / "characters",
        {
            char_id: _render_entity(_character_fm(char), char.name, char.description)
            for char_id, char in characters.items()
        },
    )
    _write_entity_folder(
        root / "items",
        {
            item_id: _render_entity(_item_fm(item), item.name, item.description)
            for item_id, item in world.items.items()
        },
    )
    return root


def load_world(root: Path) -> World:
    root = Path(root)
    world_fm, world_body = _split_frontmatter((root / "world.md").read_text(encoding="utf-8"))

    locations = _read_entity_folder(root / "locations", _parse_location)
    characters_raw = _read_entity_folder(root / "characters", _parse_character)
    items = _read_entity_folder(root / "items", _parse_item)

    player_id = world_fm.get("player_id") or ""
    if player_id not in characters_raw:
        raise ValueError(
            f"world.md references player_id {player_id!r} "
            f"but characters/{player_id}.md does not exist"
        )
    player = characters_raw.pop(player_id)

    return World(
        description=world_body.strip(),
        event_log=list(world_fm.get("event_log") or []),
        player=player,
        locations=locations,
        characters=characters_raw,
        items=items,
    )
