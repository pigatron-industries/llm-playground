"""A server-side folder browser dialog.

The app server and the browser usually run on the same machine, and project
paths are absolute paths on that machine's filesystem. Browsers don't expose
real filesystem paths from their native folder pickers (for security reasons),
so instead this walks the server's own filesystem and returns the absolute
path the user picks.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from nicegui import ui


def _resolve_start(path: str) -> Path:
    if path:
        candidate = Path(path).expanduser()
        if candidate.is_dir():
            return candidate
        if candidate.parent.is_dir():
            return candidate.parent
    return Path.home()


def _subdirs(path: Path) -> list[Path]:
    try:
        entries = [e for e in path.iterdir() if e.is_dir() and not e.name.startswith(".")]
    except (PermissionError, OSError):
        return []
    return sorted(entries, key=lambda e: e.name.lower())


async def pick_folder(start_path: str = "") -> str | None:
    """Open a dialog to browse the server's filesystem and pick a folder.

    Returns the chosen absolute path, or ``None`` if the user cancels.
    """
    result: asyncio.Future[str | None] = asyncio.get_event_loop().create_future()
    current = {"path": _resolve_start(start_path)}

    dialog = ui.dialog()
    with dialog, ui.card().classes("w-[480px]"):
        ui.label("Choose a folder").classes("text-base font-medium")
        path_label = ui.label().classes("text-xs text-gray-500 break-all")
        list_container = ui.column().classes("w-full max-h-80 overflow-y-auto gap-0 mt-1")

        def render() -> None:
            path_label.set_text(str(current["path"]))
            list_container.clear()
            with list_container:
                parent = current["path"].parent
                if parent != current["path"]:
                    ui.button("..", on_click=lambda: navigate(parent)).props(
                        "flat dense no-caps align=left"
                    ).classes("w-full justify-start")
                for entry in _subdirs(current["path"]):
                    ui.button(entry.name, icon="folder", on_click=lambda e=entry: navigate(e)).props(
                        "flat dense no-caps align=left"
                    ).classes("w-full justify-start")

        def navigate(path: Path) -> None:
            current["path"] = path
            render()

        render()

        def finish(value: str | None) -> None:
            if not result.done():
                result.set_result(value)
            dialog.close()

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Cancel", on_click=lambda: finish(None)).props("flat")
            ui.button(
                "Select this folder", on_click=lambda: finish(str(current["path"]))
            ).props("flat")

    dialog.on("hide", lambda: finish(None))
    dialog.open()
    return await result
