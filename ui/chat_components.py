from __future__ import annotations

import json
import re
import uuid
from typing import Any, Callable

from nicegui import ui

from .chat_utils import _format_time


# See chat._IMAGE_NAME_RE for naming expectations
_IMAGE_NAME_RE = re.compile(r"\bimage-\d{8}-\d{6}-\d{2}(?:-\d+)?\.png\b")


def _message_bubble(
    name: str, is_user: bool, timestamp: str | None = None, on_delete: Callable | None = None
):
    row = ui.row().classes("w-full items-start gap-3 no-wrap py-1")
    with row:
        with ui.column().classes("w-20 shrink-0 items-end gap-0 pt-2 select-none"):
            name_color = "text-[#5898d4]" if is_user else "text-gray-400"
            ui.label(name).classes(f"{name_color} text-xs font-medium")
            short, full = _format_time(timestamp)
            if short:
                ui.label(short).classes("text-[10px] text-gray-400").tooltip(full)
        with ui.element("div").classes(
            "bg-white/10 rounded-2xl px-4 py-2 grow min-w-0 relative"
        ):
            if on_delete is not None:
                with ui.element("div").classes("absolute top-0 right-0 -mt-1 -mr-1"):
                    ui.button(
                        icon="close", on_click=on_delete,
                    ).props("flat dense round size=xs color=slate").classes(
                        "opacity-20 hover:opacity-100 transition-opacity"
                    ).tooltip("Remove from history")
            bubble = ui.element("div").classes("w-full")
    bubble.row = row
    return bubble


def _assistant_tool_report(message: dict) -> tuple[str, bool]:
    calls = message.get("tool_calls") or []
    if not calls:
        return ("", False)

    lines: list[str] = []
    for call in calls:
        name = call.get("name", "unknown_tool")
        arguments = call.get("arguments", "{}")
        lines.append(f"**Tool call:**\n```\n{name}\n{arguments}\n```")
    return ("\n\n".join(lines), True)


def _parse_image_meta(line: str) -> list[dict] | None:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\[", line):
        try:
            payload, _ = decoder.raw_decode(line, match.start())
        except ValueError:
            continue
        valid = [
            item for item in (payload if isinstance(payload, list) else [])
            if isinstance(item, dict)
            and isinstance(item.get("url"), str)
            and _IMAGE_NAME_RE.fullmatch(item["url"].rsplit("/", 1)[-1])
        ]
        if valid:
            return valid
    return None


def _image_entries(text: str) -> list[dict]:
    entries: list[dict] = []
    for line in (text or "").splitlines():
        if "image_meta" not in line:
            continue
        for item in _parse_image_meta(line) or []:
            name = item["url"].rsplit("/", 1)[-1]
            entries.append(
                {
                    "url": f"/api/images/{name}",
                    "prompt": item.get("prompt") or None,
                    "negative_prompt": item.get("negative_prompt") or None,
                    "width": item.get("width") or None,
                    "height": item.get("height") or None,
                }
            )
    for name in _IMAGE_NAME_RE.findall(text or ""):
        url = f"/api/images/{name}"
        if url not in {entry["url"] for entry in entries}:
            entries.append(
                {"url": url, "prompt": None, "negative_prompt": None, "width": None, "height": None}
            )
    return entries


def _show_prompt_dialog(entry: dict) -> None:
    with ui.dialog() as dialog, ui.card().classes("w-[min(92vw,560px)] p-5"):
        ui.label("Image Prompt").classes("text-base font-semibold text-gray-100")
        if entry.get("prompt"):
            ui.label(entry["prompt"]).classes("mt-2 whitespace-pre-wrap break-words text-sm text-gray-300")
        else:
            ui.label("(no prompt recorded)").classes("mt-2 italic text-sm text-gray-500")
        if entry.get("negative_prompt"):
            ui.label("Negative Prompt").classes("mt-4 text-sm font-semibold text-gray-400")
            ui.label(entry["negative_prompt"]).classes(
                "whitespace-pre-wrap break-words text-sm text-gray-300"
            )
        if entry.get("width") and entry.get("height"):
            ui.label(f"Size: {entry['width']} × {entry['height']}").classes(
                "mt-3 text-xs text-gray-500"
            )
        with ui.row().classes("mt-5 w-full justify-end"):
            ui.button("Close", on_click=dialog.close).props("flat")
    dialog.open()


def _show_image_overlay(url: str) -> None:
    img_id = f"overlay-img-{uuid.uuid4().hex}"
    img_html = (
        f"<img id=\"{img_id}\" src=\"{url}\" "
        "style=\"display:block; height:auto; max-width:none; max-height:none;\" "
        "onload=\"(function(img){try{const dpr=window.devicePixelRatio||1;const w=img.naturalWidth/ dpr; img.style.width = w + 'px';}catch(e){} })(this)\"/>")

    with ui.dialog() as dialog:
        wrapper = (
            f'<div style="position:fixed; inset:0; display:flex; align-items:center; justify-content:center; overflow:auto; background:transparent; cursor:zoom-out">{img_html}</div>'
        )
        ui.html(wrapper).on("click", dialog.close)
    dialog.open()


def _render_image_bubbles(
    container,
    entries: list[dict],
    on_rerun: Callable[[dict, Any], Any] | None = None,
    on_context: Callable[[dict], Any] | None = None,
) -> None:
    for entry in entries:
        with container:
            with _message_bubble("Image", is_user=False):
                ui.image(entry["url"]).classes(
                    "w-full max-w-xl rounded-lg cursor-zoom-in"
                ).on("click", lambda e, u=entry["url"]: _show_image_overlay(u))
                if entry.get("prompt") or entry.get("negative_prompt"):
                    with ui.row().classes("mt-1.5"):
                        if on_rerun is not None and entry.get("prompt"):
                            rerun_btn = ui.button(
                                "Rerun",
                                icon="refresh",
                            ).props("flat dense size=sm").classes("text-gray-300")
                            # Bound after creation so the default captures the
                            # button itself (it disables itself while running).
                            rerun_btn.on(
                                "click",
                                lambda e, item=entry, btn=rerun_btn: on_rerun(item, btn),
                            )
                            rerun_btn.tooltip(
                                "Regenerate with the same prompt, size, model and LoRAs"
                            )
                        if on_context is not None and entry.get("prompt"):
                            # Sets the workflow's hidden context settings (prompt,
                            # negative prompt, size) to this image's parameters so
                            # the next generation starts from them — no regeneration.
                            context_btn = ui.button(
                                "Context",
                                icon="tune",
                            ).props("flat dense size=sm").classes("text-gray-300")
                            context_btn.on(
                                "click",
                                lambda e, item=entry: on_context(item),
                            )
                            context_btn.tooltip(
                                "Use this image's prompt, negative prompt and size as the workflow context"
                            )
                        ui.button(
                            "Prompt",
                            icon="description",
                            on_click=lambda e, item=entry: _show_prompt_dialog(item),
                        ).props("flat dense size=sm").classes("text-gray-300")
