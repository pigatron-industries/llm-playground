"""NiceGUI chat interface.

Full-screen layout:
  * left sidebar  — projects and the list of stored chats
  * right sidebar — the loaded chat's workflow settings, rendered live from
    the workflow's schema and editable at any time; the workflow choice
    itself locks once the chat has a first message (see ``update_chat``)
  * center        — scrolling chat history
  * bottom        — input box + send button

Chats are persisted server-side (see ``api/store.py``), each pinned to a
workflow (see ``api/workflows``) that owns its model settings and chat loop.
The UI remembers the current chat id in browser storage, so a refresh reloads
the full conversation from the backend.
"""

from __future__ import annotations

import asyncio
import json
import uuid
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx
from nicegui import app, ui

from api.config import DEFAULT_MODEL_BASES

from . import client
from .path_picker import pick_folder


def _error_detail(exc: Exception) -> str:
    """Pull a human-readable message out of an httpx error."""
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            return exc.response.json().get("detail", exc.response.text)
        except Exception:
            return exc.response.text
    return str(exc)


def _format_time(iso: str | None) -> tuple[str, str]:
    """Return (short, full) local-time strings for an ISO timestamp."""
    if not iso:
        return ("", "")
    local = datetime.fromisoformat(iso).astimezone()
    return (local.strftime("%H:%M"), local.strftime("%Y-%m-%d %H:%M:%S"))


def _message_bubble(
    name: str, is_user: bool, timestamp: str | None = None, on_delete: Callable | None = None
):
    """Render a message row: participant name (+ time) on the left, bubble right.

    Returns the (empty) bubble element so the caller can fill it with the
    message text or a spinner.
    """
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
    bubble.row = row  # stashed so callers can drop the whole row if it ends up unused
    return bubble


def _assistant_tool_report(message: dict) -> tuple[str, bool]:
    """Render persisted assistant tool-call metadata. Returns (text, is_tool_report)."""
    calls = message.get("tool_calls") or []
    if not calls:
        return ("", False)

    lines: list[str] = []
    for call in calls:
        name = call.get("name", "unknown_tool")
        arguments = call.get("arguments", "{}")
        lines.append(f"**Tool call:**\n```\n{name}\n{arguments}\n```")
    return ("\n\n".join(lines), True)


# The naming ``_save_image`` uses (image-YYYYMMDD-HHMMSS-NN.png, plus an
# optional -N collision suffix). Matching the bare filename — rather than a
# specific URL form — means older persisted tool results that stored the
# absolute path keep rendering too.
_IMAGE_NAME_RE = re.compile(r"\bimage-\d{8}-\d{6}-\d{2}(?:-\d+)?\.png\b")


def _parse_image_meta(line: str) -> list[dict] | None:
    """Extract the ``[image_meta]`` JSON payload from a tool-result line.

    Each item maps an image URL to its prompt / negative prompt. Returns the
    valid items, or ``None`` when the line carries no usable metadata."""
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
    """Per-image entries (``url``, ``prompt``, ``negative_prompt``) for a tool result.

    New results carry an ``[image_meta]`` JSON line; older persisted results
    only mention the filename, so those fall back to entries without prompts."""
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
                }
            )
    for name in _IMAGE_NAME_RE.findall(text or ""):
        url = f"/api/images/{name}"
        if url not in {entry["url"] for entry in entries}:
            entries.append({"url": url, "prompt": None, "negative_prompt": None})
    return entries


def _show_prompt_dialog(entry: dict) -> None:
    """Popup showing the prompt(s) recorded for one generated image."""
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
        with ui.row().classes("mt-5 w-full justify-end"):
            ui.button("Close", on_click=dialog.close).props("flat")
    dialog.open()


def _show_image_overlay(url: str) -> None:
    """Full-screen image lightbox.

    The image is scaled to fill the viewport (preserving aspect ratio) so it shows
    at full resolution — larger than the bubble it was clicked from. Clicking
    anywhere on the overlay dismisses it."""
    # Show the image at its natural size (1:1) when possible. Center it
    # and allow scrolling/panning if the image is larger than the viewport
    # so it is not cropped. Clicking anywhere dismisses the overlay.
    # Use a raw <img> with an onload handler that sets the displayed size to
    # the image's natural pixel dimensions adjusted for devicePixelRatio
    # (so a 1024px-wide image on a 2x DPR screen will display as 512 CSS px)
    # and allow scrolling when the image is larger than the viewport.
    img_id = f"overlay-img-{uuid.uuid4().hex}"
    img_html = (
        f"<img id=\"{img_id}\" src=\"{url}\" "
        "style=\"display:block; height:auto; max-width:none; max-height:none;\" "
        "onload=\"(function(img){try{const dpr=window.devicePixelRatio||1;const w=img.naturalWidth/ dpr; img.style.width = w + 'px';}catch(e){} })(this)\"/>")

    with ui.dialog() as dialog:
        wrapper = (
            f'<div style="position:fixed; inset:0; display:flex; align-items:center; justify-content:center; overflow:auto; background:transparent; cursor:zoom-out" onclick="if(event.target===this) this.closest(\'dialog\').close();">{img_html}</div>'
        )
        ui.html(wrapper)
    dialog.open()


def _render_image_bubbles(container, entries: list[dict]) -> None:
    """One standalone bubble per generated image (see ``_image_entries``).

    Each image is clickable — it opens a full-size overlay
    (``_show_image_overlay``) that a second click dismisses. Images recorded
    with prompt metadata also get a flat "Prompt" button that opens
    ``_show_prompt_dialog``. Must be called inside an explicit
    ``with <container>:`` block."""
    for entry in entries:
        with container:
            with _message_bubble("Image", is_user=False):
                ui.image(entry["url"]).classes(
                    "w-full max-w-xl rounded-lg cursor-zoom-in"
                ).on("click", lambda e, u=entry["url"]: _show_image_overlay(u))
                if entry.get("prompt") or entry.get("negative_prompt"):
                    with ui.row().classes("mt-1.5"):
                        ui.button(
                            "Prompt",
                            icon="description",
                            on_click=lambda e, item=entry: _show_prompt_dialog(item),
                        ).props("flat dense size=sm").classes("text-gray-300")


def _default_settings(
    schema: dict,
    models: list[str],
    projects: list[dict] | None = None,
    selected_project_id: str | None = None,
    stored_settings: dict | None = None,
) -> dict:
    """Best-effort default value per field, for creating a chat or switching
    a chat to a workflow it's never used before (no persisted values yet).

    Uses stored_settings if provided, falling back to schema defaults.

    ``project_select`` fields are the exception: they always reflect
    whichever project is currently selected in the left sidebar and are
    never taken from ``stored_settings`` — a project field is contextual to
    "what you're looking at right now", not a preference to remember.
    """
    projects = projects or []
    stored_settings = stored_settings or {}
    settings: dict = {}
    for field_name, field_schema in schema.get("properties", {}).items():
        widget = field_schema.get("widget")
        if widget == "project_select":
            settings[field_name] = selected_project_id or (
                projects[0]["id"] if projects else ""
            )
        elif field_name in stored_settings:
            settings[field_name] = stored_settings[field_name]
        elif "default" in field_schema:
            settings[field_name] = field_schema["default"]
        elif widget == "model_select":
            settings[field_name] = models[0] if models else ""
        elif field_schema.get("type") == "boolean":
            settings[field_name] = False
        elif field_schema.get("type") in ("integer", "number"):
            settings[field_name] = 0
        else:
            settings[field_name] = ""
    return settings


def _project_select_fields(schema: dict) -> set[str]:
    """Names of a workflow settings schema's ``project_select`` fields."""
    return {
        name
        for name, field_schema in schema.get("properties", {}).items()
        if field_schema.get("widget") == "project_select"
    }


def _render_settings_field(
    field_name: str,
    field_schema: dict,
    models: list[str],
    projects: list[dict],
    value: Any,
    on_change: Callable[[], None] | None,
) -> Callable[[], Any]:
    """Render one input for a workflow settings field; return a getter for its value.

    The widget is picked from the field's JSON Schema shape (type / enum),
    with an optional ``widget`` hint (set via ``Field(json_schema_extra=...)``
    on the workflow's settings model) overriding the default — e.g. a plain
    string field renders as a single-line input unless it's hinted
    "textarea", "model_select" binds to the live model list, and
    "project_select" binds to the project list from the left sidebar, rather
    than free text. ``on_change`` (if given) is wired to fire whenever the
    field's value settles — immediately for discrete choices (select/
    checkbox/number), on blur for free text, so it isn't fired on every
    keystroke.
    """
    label = field_schema.get("title") or field_name.replace("_", " ").title()
    description = field_schema.get("description")
    widget = field_schema.get("widget")

    if widget == "model_select":
        select = ui.select(options=models, value=value, label=label, with_input=True).classes(
            "w-full"
        )
        if description:
            select.tooltip(description)
        if on_change:
            select.on_value_change(on_change)
        return lambda: select.value

    if widget == "project_select":
        options = {p["id"]: p["name"] for p in projects}
        select = ui.select(options=options, value=value, label=label).classes("w-full")
        if description:
            select.tooltip(description)
        if on_change:
            select.on_value_change(on_change)
        return lambda: select.value

    if "enum" in field_schema:
        select = ui.select(options=field_schema["enum"], value=value, label=label).classes(
            "w-full"
        )
        if on_change:
            select.on_value_change(on_change)
        return lambda: select.value

    field_type = field_schema.get("type")

    if field_type == "boolean":
        checkbox = ui.checkbox(label, value=bool(value))
        if on_change:
            checkbox.on_value_change(on_change)
        return lambda: checkbox.value

    if field_type in ("integer", "number"):
        number = ui.number(
            label,
            value=value if value is not None else 0,
            min=field_schema.get("minimum"),
            max=field_schema.get("maximum"),
        ).classes("w-full")
        if on_change:
            number.on_value_change(on_change)
        return lambda: number.value

    if widget == "textarea":
        area = (
            ui.textarea(label, value=value or "", placeholder=description)
            .classes("w-full")
            .props("outlined autogrow dense")
        )
        if on_change:
            area.on("blur", on_change)
        return lambda: area.value

    text_field = ui.input(label, value=value or "", placeholder=description).classes("w-full")
    if on_change:
        text_field.on("blur", on_change)
    return lambda: text_field.value


def _render_image_model_picker(
    base_schema: dict,
    model_schema: dict,
    base_value: Any,
    model_value: Any,
    on_change: Callable[[], None] | None,
) -> tuple[Callable[[], Any], Callable[[], Any]]:
    """Render the image base + model pickers as one coordinated unit.

    The base select is fixed (``DEFAULT_MODEL_BASES``); the model select is
    populated from the image API (``client.get_image_models(base)``) and
    re-fetched whenever the base changes. Returns ``(base_getter, model_getter)``.
    """
    base_label = base_schema.get("title") or "Image base"
    model_label = model_schema.get("title") or "Image model"
    base_description = base_schema.get("description")
    model_description = model_schema.get("description")

    # A base the user has never set (None / unknown) falls back to the first.
    base_value = base_value if base_value in DEFAULT_MODEL_BASES else DEFAULT_MODEL_BASES[0]
    base_select = ui.select(
        options=list(DEFAULT_MODEL_BASES), value=base_value, label=base_label
    ).classes("w-full")
    if base_description:
        base_select.tooltip(base_description)

    # The select starts empty (a value not in ``options`` is rejected by
    # NiceGUI). ``last_known_model`` remembers the stored value so a save issued
    # before the async load finishes still reads it, not a transient empty one.
    model_select = ui.select(
        options=[], value=None, label=model_label, with_input=True
    ).classes("w-full")
    if model_description:
        model_select.tooltip(model_description)
    last_known_model = model_value or ""

    async def _load_models(base: str | None, keep: str | None = None, save: bool = False) -> None:
        try:
            model_select.options = []
            model_select.value = None
            model_select.update()
            data = await client.get_image_models(base)
            names = [m["name"] for m in data.get("models", []) if m.get("name")]
            model_select.options = names
            model_select.value = keep if keep in names else (names[0] if names else None)
            model_select.update()
            nonlocal last_known_model
            last_known_model = model_select.value or ""
        except Exception as exc:  # noqa: BLE001
            ui.notify(f"Could not load image models: {_error_detail(exc)}", type="negative")
        if save and on_change is not None:
            await on_change()

    async def _on_base_change(e: Any) -> None:
        # A new base was picked — reload its models and drop the old selection.
        await _load_models(e.value, keep=None, save=True)

    base_select.on_value_change(_on_base_change)

    # Populate the model list for the currently selected base (don't save yet).
    try:
        asyncio.get_running_loop().create_task(
            _load_models(base_value, keep=model_value, save=False)
        )
    except RuntimeError:  # no running loop; the next base change will load
        pass

    return (lambda: base_select.value or "", lambda: model_select.value or last_known_model or "")


def _render_settings_form(
    schema: dict,
    models: list[str],
    projects: list[dict],
    values: dict,
    on_change: Callable[[], None] | None = None,
) -> dict[str, Callable[[], Any]]:
    """Render inputs for every field in a workflow's settings schema, seeded
    from ``values`` (falling back to each field's schema default).

    Must be called inside a ``with <container>:`` block so the widgets attach
    to the right place. Returns one value-getter per field name.

    The image base/model pair is special-cased: the base select and the model
    select are rendered together as one coordinated picker (the model list is
    fetched from the image API for the chosen base, and re-fetched when the
    base changes) rather than as two independent fields — see
    ``_render_image_model_picker``.
    """
    props = schema.get("properties", {})
    getters: dict[str, Callable[[], Any]] = {}
    handled: set[str] = set()
    for field_name, field_schema in props.items():
        if field_schema.get("widget") == "image_base_select":
            model_field = next(
                (
                    n
                    for n, s in props.items()
                    if s.get("widget") == "image_model_select"
                ),
                None,
            )
            if model_field is None:
                continue
            base_getter, model_getter = _render_image_model_picker(
                field_schema,
                props[model_field],
                values.get(field_name, field_schema.get("default")),
                values.get(model_field),
                on_change,
            )
            getters[field_name] = base_getter
            getters[model_field] = model_getter
            handled.add(model_field)
            continue
        if field_name in handled:
            continue
        getters[field_name] = _render_settings_field(
            field_name,
            field_schema,
            models,
            projects,
            values.get(field_name, field_schema.get("default")),
            on_change,
        )
    return getters


def register_pages() -> None:
    @ui.page("/")
    async def chat_page() -> None:
        # Rendered conversation (mirror of the server-side chat) and the id of
        # the chat currently open.
        history: list[dict] = []
        workflows: list[dict] = []
        # Identifies whichever live-stream view (see make_stream_view) is
        # currently allowed to render into messages_col, plus the network
        # task (send()'s client.stream_message / reattach_if_streaming's
        # client.reattach_stream) currently feeding it. Reset by every
        # set_history() call — i.e. every time we switch to showing a
        # different (or freshly reloaded) chat — so a send()/reattach task
        # left over from before a chat switch can never write into the DOM
        # again, even if the user navigates back to the very same chat
        # (which mints a fresh token of its own via reattach_if_streaming).
        # set_history() also cancels the outgoing task so its connection is
        # closed right away rather than left open until that generation
        # finishes on its own — generation itself is unaffected, since it
        # keeps running server-side independent of any one subscriber.
        active_view: dict = {"token": None, "task": None}
        state: dict = {
            "chat_id": None,
            "project_id": None,
            "context_lengths": {},
            "models": [],
            "workflow_id": None,
            "workflow_settings": {},
            "extra_context_chars": 0,
        }

        def _stored_chat_id() -> str | None:
            try:
                return app.storage.user.get("chat_id")
            except Exception:  # noqa: BLE001 — storage may be unavailable
                return None

        def _store_chat_id(chat_id: str | None) -> None:
            try:
                app.storage.user["chat_id"] = chat_id
            except Exception:  # noqa: BLE001
                pass

        def _stored_project_id() -> str | None:
            try:
                return app.storage.user.get("project_id")
            except Exception:  # noqa: BLE001 — storage may be unavailable
                return None

        def _store_project_id(project_id: str | None) -> None:
            try:
                app.storage.user["project_id"] = project_id
            except Exception:  # noqa: BLE001
                pass

        def _stored_workflow_settings() -> dict:
            try:
                return app.storage.user.get("workflow_settings") or {}
            except Exception:  # noqa: BLE001
                return {}

        def _store_workflow_settings(settings: dict) -> None:
            try:
                app.storage.user["workflow_settings"] = settings
            except Exception:  # noqa: BLE001
                pass

        ui.query("body").classes("m-0").style("background-color: rgb(24, 28, 33)")
        ui.colors(primary="#5898d4")
        # Force an explicit full-viewport height down the layout chain so the
        # scroll area's flex-grow has real height to fill. Quasar only sets a
        # min-height on q-page, which flex-grow/height:100% can't chain off.
        ui.add_css(
            """
            .q-page-container { height: 100vh; }
            .q-page { height: 100%; }
            .nicegui-content { height: 100%; padding: 0; gap: 0; }
            """
        )

        # --- Header -------------------------------------------------------
        with ui.header().classes("items-center justify-between bg-indigo-900"):
            ui.label("LLM Test Harness").classes("text-lg font-semibold")
            provider_label = ui.label("").classes("text-sm opacity-80")

        # --- Left sidebar -------------------------------------------------
        with ui.left_drawer(bordered=True).classes("bg-[#2b323b]").props("width=280"):
            with ui.row().classes("w-full items-center justify-between no-wrap"):
                ui.label("Projects").classes("text-sm font-medium text-gray-300")
                ui.button(icon="add", on_click=lambda: open_add_project_dialog()).props(
                    "flat dense round size=sm"
                ).tooltip("Add project")
            project_list = ui.column().classes("w-full gap-1 mt-2")

            ui.separator().classes("w-full my-3")

            with ui.row().classes("w-full items-center justify-between no-wrap"):
                ui.label("Chats").classes("text-sm font-medium text-gray-300")
                ui.button(icon="add", on_click=lambda: new_chat()).props(
                    "flat dense round size=sm"
                )
            chat_list = ui.column().classes("w-full gap-1")
            # chat_id -> spinner element for that row, so the periodic
            # is_streaming poll (see poll_streaming_indicators) can just
            # toggle visibility in place rather than tearing down and
            # rebuilding the whole list every few seconds — doing that while
            # e.g. an edit-title dialog is open was closing the dialog.
            streaming_spinners: dict[str, Any] = {}

        # --- Right sidebar: workflow settings ------------------------------
        with ui.right_drawer(bordered=True).classes("bg-[#2b323b]").props("width=280"):

            async def refresh_models() -> None:
                try:
                    data = await client.get_models()
                except Exception as exc:  # noqa: BLE001
                    ui.notify(
                        f"Could not load models: {_error_detail(exc)}",
                        type="negative",
                        multi_line=True,
                    )
                    return
                state["models"] = data.get("models", [])
                state["context_lengths"] = data.get("context_lengths", {})
                update_context_usage()

            with ui.row().classes("w-full items-center justify-between no-wrap"):
                ui.label("Workflow").classes("text-sm font-medium text-gray-300")
                ui.button(icon="refresh", on_click=lambda: refresh_model_options()).props(
                    "flat dense round size=sm"
                ).tooltip("Refresh models")
            # Locked once the chat has a first message (see PATCH /chats/{id});
            # the fields below it stay editable for the life of the chat.
            workflow_select = ui.select(options={}, label="Workflow").classes("w-full")
            settings_container = ui.column().classes("w-full gap-2 mt-1")

            ui.separator().classes("w-full my-2")

            # --- Context window ------------------------------------------
            ui.label("Context").classes("text-sm font-medium text-gray-300")
            usage_bar = ui.linear_progress(value=0.0, show_value=False).props(
                "rounded"
            ).classes("w-full")
            usage_label = ui.label("").classes("text-xs text-gray-500")

        settings_getters: dict = {}

        def _without_project_fields(workflow_id: str | None, settings: dict) -> dict:
            """Drop ``project_select`` fields — they're contextual to
            whichever project is currently selected, never a stored default."""
            info = next((w for w in workflows if w["id"] == workflow_id), None)
            if info is None:
                return dict(settings)
            fields = _project_select_fields(info.get("settings_schema", {}))
            return {k: v for k, v in settings.items() if k not in fields}

        async def save_settings() -> None:
            if not state["chat_id"]:
                return
            settings = {name: getter() for name, getter in settings_getters.items()}
            try:
                updated = await client.update_chat(state["chat_id"], workflow_settings=settings)
            except Exception as exc:  # noqa: BLE001
                ui.notify(f"Could not save settings: {_error_detail(exc)}", type="negative")
                return
            state["workflow_settings"] = updated.get("workflow_settings") or {}
            _store_workflow_settings(_without_project_fields(state["workflow_id"], settings))
            await _save_project_workflow_defaults()
            await refresh_context_estimate()

        async def _save_project_workflow_defaults() -> None:
            """Remember the current chat's workflow + settings on whichever
            project is selected in the left sidebar, so new chats started
            for that project default to them next time."""
            project_id = state["project_id"]
            if not project_id:
                return
            try:
                updated = await client.update_project(
                    project_id,
                    default_workflow_id=state["workflow_id"],
                    default_workflow_settings=_without_project_fields(
                        state["workflow_id"], state["workflow_settings"]
                    ),
                )
            except Exception:  # noqa: BLE001 — best effort, don't block the UI on this
                return
            for i, p in enumerate(projects):
                if p.get("id") == project_id:
                    projects[i] = updated
                    break

        def render_workflow_settings() -> None:
            """(Re)build the settings fields for the loaded chat's workflow,
            seeded from its currently persisted values."""
            nonlocal settings_getters
            settings_container.clear()
            info = next((w for w in workflows if w["id"] == state.get("workflow_id")), None)
            if info is None:
                settings_getters = {}
                return
            with settings_container:
                settings_getters = _render_settings_form(
                    info["settings_schema"],
                    state["models"],
                    _real_projects(),
                    state.get("workflow_settings") or {},
                    on_change=save_settings,
                )

        async def refresh_model_options() -> None:
            await refresh_models()
            if state["chat_id"]:
                render_workflow_settings()

        async def on_workflow_change() -> None:
            new_workflow_id = workflow_select.value
            if not state["chat_id"] or new_workflow_id == state.get("workflow_id"):
                return
            info = next((w for w in workflows if w["id"] == new_workflow_id), None)
            if info is None:
                return
            defaults = _default_settings(
                info["settings_schema"],
                state["models"],
                _real_projects(),
                state["project_id"],
                _stored_workflow_settings(),
            )
            try:
                updated = await client.update_chat(
                    state["chat_id"], workflow_id=new_workflow_id, workflow_settings=defaults
                )
            except Exception as exc:  # noqa: BLE001
                ui.notify(f"Could not switch workflow: {_error_detail(exc)}", type="negative")
                workflow_select.value = state.get("workflow_id")
                return
            state["workflow_id"] = updated.get("workflow_id")
            state["workflow_settings"] = updated.get("workflow_settings") or {}
            render_workflow_settings()
            await _save_project_workflow_defaults()
            await refresh_context_estimate()

        workflow_select.on_value_change(on_workflow_change)

        def apply_chat_to_sidebar() -> None:
            """Sync the workflow select + settings form to ``state`` — call
            after loading, creating, or clearing the current chat."""
            workflow_select.options = {w["id"]: w["name"] for w in workflows}
            workflow_select.value = state.get("workflow_id")
            workflow_select.update()
            if state["chat_id"] and not history:
                workflow_select.enable()
            else:
                workflow_select.disable()
            render_workflow_settings()

        # --- Context usage -----------------------------------------------
        def _chars_to_tokens(chars: int) -> int:
            # Rough heuristic (~4 chars/token). Exact tokenisation is
            # model-specific, but this is close enough to gauge how full the
            # context window is — hence the "≈" in the display.
            return (chars + 3) // 4 if chars else 0

        def _estimate_tokens(text: str) -> int:
            return _chars_to_tokens(len(text)) if text else 0

        def _selected_context_length() -> int | None:
            model = (state.get("workflow_settings") or {}).get("model")
            return state["context_lengths"].get(model) if model else None

        async def refresh_context_estimate() -> None:
            """Fetch the current workflow's extra-context size (system
            prompt, injected project files, ...) — each workflow computes
            this its own way (see ``Workflow.extra_context_chars``), so the
            UI can't derive it generically from ``workflow_settings``."""
            if not state["chat_id"]:
                state["extra_context_chars"] = 0
                return
            try:
                data = await client.get_context_estimate(state["chat_id"])
            except Exception:  # noqa: BLE001 — best-effort; leave prior estimate
                return
            state["extra_context_chars"] = data.get("extra_context_chars", 0)
            update_context_usage()

        def _conversation_tokens() -> int:
            """Estimated tokens the next request will carry: the workflow's
            extra context (last fetched via ``refresh_context_estimate``) +
            stored history + whatever is currently typed in the input box."""
            total = _chars_to_tokens(state.get("extra_context_chars", 0))
            for msg in history:
                total += _estimate_tokens(msg.get("content", ""))
            total += _estimate_tokens((text_input.value or "").strip())
            return total

        def update_context_usage() -> None:
            limit = _selected_context_length()
            used = _conversation_tokens()
            if limit:
                frac = used / limit
                usage_bar.set_visibility(True)
                usage_bar.value = min(frac, 1.0)
                near = frac >= 0.9
                usage_bar.props(f"color={'red' if near else 'primary'}")
                usage_label.text = f"≈ {used:,} / {limit:,} tokens ({frac * 100:.0f}%)"
                usage_label.classes(
                    replace="text-xs " + ("text-[#ff0000]" if near else "text-gray-500")
                )
            else:
                usage_bar.set_visibility(False)
                usage_label.text = f"≈ {used:,} tokens"
                usage_label.classes(replace="text-xs text-gray-500")

        # --- Center: chat history ----------------------------------------
        messages_area = ui.scroll_area().classes("w-full flex-grow min-h-0")
        with messages_area:
            messages_col = ui.column().classes("w-full max-w-5xl mx-auto gap-2 p-4")

        projects: list[dict[str, str]] = []

        def _real_projects() -> list[dict[str, str]]:
            """``projects`` includes a synthetic "No project" placeholder
            (id=None) for the sidebar list; workflow settings forms only want
            the actual projects."""
            return [p for p in projects if p.get("id")]

        async def load_projects() -> None:
            try:
                projects[:] = await client.list_projects()
            except Exception:  # noqa: BLE001
                projects[:] = []
            displayed_projects = [{"id": None, "name": "No project", "path": ""}] + projects
            stored_project_id = _stored_project_id()
            if stored_project_id and any(p.get("id") == stored_project_id for p in projects):
                state["project_id"] = stored_project_id
            else:
                state["project_id"] = None
                _store_project_id(None)
            projects[:] = displayed_projects
            render_projects()
            await render_chat_list()

        async def select_project(project_id: str | None) -> None:
            state["project_id"] = project_id
            _store_project_id(project_id)
            render_projects()
            await render_chat_list()

        async def browse_folder(path_input) -> None:
            chosen = await pick_folder(path_input.value)
            if chosen is not None:
                path_input.value = chosen

        def open_add_project_dialog() -> None:
            dialog = ui.dialog()
            with dialog, ui.card().classes("w-[360px]"):
                ui.label("Add project").classes("text-base font-medium")
                project_name_input = ui.input("Name", placeholder="My project").classes(
                    "w-full"
                )
                with ui.row().classes("w-full items-end gap-2"):
                    project_path_input = ui.input(
                        "Folder", placeholder="/path/to/project"
                    ).classes("flex-grow")
                    ui.button(icon="folder_open", on_click=lambda: browse_folder(project_path_input)).props(
                        "flat dense round"
                    ).tooltip("Browse for folder")
                with ui.row().classes("w-full justify-end gap-2 mt-2"):
                    ui.button("Cancel", on_click=dialog.close).props("flat")
                    ui.button(
                        "Add",
                        on_click=lambda: add_project(
                            project_name_input.value, project_path_input.value, dialog
                        ),
                    ).props("flat")
            dialog.open()

        async def add_project(
            name: str | None = None, path: str | None = None, dialog=None
        ) -> None:
            clean_name = (name or "").strip()
            clean_path = (path or "").strip()
            if not clean_name or not clean_path:
                ui.notify("Enter both a project name and folder path.", type="warning")
                return
            try:
                created = await client.create_project(clean_name, clean_path)
            except Exception as exc:  # noqa: BLE001
                ui.notify(f"Could not save project: {_error_detail(exc)}", type="negative")
                return
            projects.append(created)
            await select_project(created["id"])
            if dialog is not None:
                dialog.close()

        def render_projects() -> None:
            project_list.clear()
            with project_list:
                if not projects:
                    ui.label("No projects yet").classes("text-xs text-gray-400")
                    return
                for project in projects:
                    current = project.get("id") == state["project_id"]
                    row_bg = "bg-[#5898d4]/30" if current else "hover:bg-white/10"
                    with ui.row().classes(
                        f"w-full items-center no-wrap gap-0 rounded {row_bg}"
                    ):
                        ui.button(
                            project.get("name", "Untitled"),
                            on_click=lambda pid=project.get("id"): select_project(pid),
                        ).props("flat dense no-caps align=left").classes(
                            "flex-grow min-w-0 justify-start normal-case ellipsis"
                        )
                        if project.get("id") is not None:
                            ui.button(
                                icon="edit",
                                on_click=lambda _, p=project: edit_project(p),
                            ).props("flat dense round size=sm").classes("text-gray-400").tooltip("Edit project")
                            ui.button(
                                icon="delete",
                                on_click=lambda _, p=project: remove_project(p),
                            ).props("flat dense round size=sm").classes("text-gray-400")


        def edit_project(project: dict[str, str]) -> None:
            dialog = ui.dialog()
            with dialog, ui.card().classes("w-[360px]"):
                ui.label("Edit Project").classes("text-base font-medium")
                project_name_input = ui.input("Name", value=project.get("name", "")).classes("w-full")
                with ui.row().classes("w-full items-end gap-2"):
                    project_path_input = ui.input(
                        "Folder", value=project.get("path", "")
                    ).classes("flex-grow")
                    ui.button(icon="folder_open", on_click=lambda: browse_folder(project_path_input)).props(
                        "flat dense round"
                    ).tooltip("Browse for folder")
                with ui.row().classes("w-full justify-end gap-2 mt-2"):
                    ui.button("Cancel", on_click=dialog.close).props("flat")
                    ui.button(
                        "Save",
                        on_click=lambda: save_project_edit(
                            project.get("id"), project_name_input.value, project_path_input.value, dialog
                        ),
                    ).props("flat")
            dialog.open()
            # Focus the name input when dialog opens
            ui.timer(0.1, lambda: project_name_input.focus(), once=True)


        async def save_project_edit(project_id: str, name: str, path: str, dialog) -> None:
            clean_name = (name or "").strip()
            clean_path = (path or "").strip()
            if not clean_name or not clean_path:
                ui.notify("Enter both a project name and folder path.", type="warning")
                return
            try:
                updated = await client.update_project(project_id, name=clean_name, path=clean_path)
            except Exception as exc:  # noqa: BLE001
                ui.notify(f"Could not update project: {_error_detail(exc)}", type="negative")
                return
            # Update the local projects list
            for i, p in enumerate(projects):
                if p.get("id") == project_id:
                    projects[i] = updated
                    break
            dialog.close()
            render_projects()
            ui.notify("Project updated.", type="positive")


        async def remove_project(project: dict[str, str]) -> None:
            project_id = project.get("id")
            if not project_id:
                return
            try:
                await client.delete_project(project_id)
            except Exception as exc:  # noqa: BLE001
                ui.notify(f"Could not delete project: {_error_detail(exc)}", type="negative")
                return
            try:
                projects.remove(project)
            except ValueError:
                pass
            if project.get("id") == state["project_id"]:
                await select_project(None)  # <-- Clears selection & refreshes list
            else:
                render_projects()

        async def delete_message_at(idx: int) -> None:
            """Delete a message from the chat history by its index."""
            if not state["chat_id"]:
                return
            try:
                await client.delete_message(state["chat_id"], idx)
            except Exception as exc:  # noqa: BLE001
                ui.notify(f"Could not delete message: {_error_detail(exc)}", type="negative")
                return
            # Remove from local history and re-render
            try:
                history.pop(idx)
            except IndexError:
                pass
            await render_history()
            messages_area.scroll_to(percent=1.0)

        async def render_history() -> None:
            messages_col.clear()
            with messages_col:
                if not history:
                    placeholder = (
                        "Start the conversation below."
                        if state["chat_id"]
                        else 'No chat selected — click "+" next to Chats to create one.'
                    )
                    ui.label(placeholder).classes("text-gray-400 text-center w-full mt-8")

                # Track the index of the last assistant message for world state
                last_assistant_idx = -1
                for idx, msg in enumerate(history):
                    if msg["role"] == "assistant":
                        last_assistant_idx = idx

                for idx, msg in enumerate(history):
                    is_user = msg["role"] == "user"
                    on_delete = lambda _, i=idx: delete_message_at(i)
                    if msg["role"] == "tool":
                        content = msg.get("content", "")
                        with _message_bubble("Tool", is_user=False, timestamp=msg.get("created_at"), on_delete=on_delete):
                            with ui.expansion("Tool Result", icon="build").classes("w-full"):
                                ui.markdown(
                                    f"**Result**\n\n```\n{content}\n```",
                                    extras=["fenced-code-blocks"],
                                ).classes("text-gray-200 break-words max-w-full")
                        # Generated images get their own bubble(s), with a
                        # Prompt button when the result recorded one.
                        _render_image_bubbles(messages_col, _image_entries(content))
                        continue

                    if msg["role"] == "assistant":
                        tool_report, is_tool = _assistant_tool_report(msg)
                        content = msg.get("content") or ""
                        reasoning = msg.get("reasoning") or ""
                        body_parts = []  # List of (text, is_tool_report)
                        if is_tool:
                            body_parts.append((tool_report, True))
                        if content.strip():
                            body_parts.append((content, False))
                        if not body_parts and not reasoning:
                            continue
                    else:
                        body_parts = [(msg.get("content", ""), False)]
                        reasoning = ""

                    with _message_bubble(
                        "You" if is_user else "Assistant",
                        is_user,
                        msg.get("created_at"),
                        on_delete=on_delete,
                    ):
                        if is_user:
                            # Keep user input verbatim (no markdown interpretation).
                            ui.label(body_parts[0][0]).classes(
                                "text-gray-200 whitespace-pre-wrap break-words"
                            )
                        else:
                            # Render assistant replies. Tool reports are verbatim, content is markdown.
                            with ui.column().classes("w-full gap-2"):
                                if reasoning:
                                    with ui.expansion("Thinking", icon="psychology").classes(
                                        "w-full text-gray-500 text-sm"
                                    ):
                                        ui.markdown(reasoning).classes(
                                            "text-gray-500 text-sm italic break-words"
                                        )
                                for part_text, is_tool_part in body_parts:
                                    if is_tool_part:
                                        with ui.expansion("Tool Use", icon="build").classes("w-full"):
                                            ui.markdown(
                                                part_text,
                                                extras=["fenced-code-blocks", "tables"],
                                            ).classes("text-gray-200 break-words max-w-full")
                                    else:
                                        ui.markdown(
                                            part_text,
                                            extras=["fenced-code-blocks", "tables"],
                                        ).classes("text-gray-200 break-words max-w-full")

                    # After the last assistant message, append state for workflows that support it.
                    if idx == last_assistant_idx and state["chat_id"]:
                        current_workflow = next(
                            (w for w in workflows if w["id"] == state["workflow_id"]),
                            None,
                        )
                        if current_workflow and current_workflow.get("has_state"):
                            try:
                                wf_state = await client.get_chat_state(state["chat_id"])
                            except Exception:  # noqa: BLE001
                                wf_state = {}
                            if wf_state:
                                with _message_bubble("State", is_user=False):
                                    with ui.column().classes("w-full gap-1"):
                                        if "location_name" in wf_state:
                                            ui.label(
                                                f"📍 {wf_state['location_name']} ({wf_state.get('location_id', '?')})"
                                            ).classes("text-xs text-gray-500")
                                        if "ascii_map" in wf_state:
                                            ui.markdown(
                                                f"```\n{wf_state['ascii_map']}\n```",
                                                extras=["fenced-code-blocks"],
                                            ).classes("text-[10px] text-gray-500 font-mono")

        async def render_chat_list() -> None:
            try:
                chats = await client.list_chats(state["project_id"])
            except Exception:  # noqa: BLE001
                return
            chat_list.clear()
            streaming_spinners.clear()
            with chat_list:
                if not chats:
                    ui.label("No chats yet").classes("text-xs text-gray-400")
                for c in chats:
                    current = c["id"] == state["chat_id"]
                    row_bg = "bg-[#5898d4]/30" if current else "hover:bg-white/10"
                    with ui.row().classes(
                        f"w-full items-center no-wrap gap-0 rounded {row_bg}"
                    ):
                        ui.button(
                            c["title"], on_click=lambda cid=c["id"]: open_chat(cid)
                        ).props("flat dense no-caps align=left").classes(
                            "flex-grow min-w-0 justify-start normal-case ellipsis"
                        )
                        spinner = ui.spinner(size="1em", color="primary").classes("shrink-0 mr-1")
                        spinner.tooltip("Generating…")
                        spinner.set_visibility(bool(c.get("is_streaming")))
                        streaming_spinners[c["id"]] = spinner
                        if c.get("is_streaming"):
                            # A stream is already going (e.g. discovered on
                            # initial page load) — make sure the poll is
                            # running so this spinner clears once it's done.
                            _wake_stream_poll()
                        ui.button(
                            icon="edit", on_click=lambda cid=c["id"], title=c["title"]: edit_chat_title(cid, title)
                        ).props("flat dense round size=sm").classes("text-gray-400").tooltip("Edit title")
                        ui.button(
                            icon="delete", on_click=lambda cid=c["id"]: remove_chat(cid)
                        ).props("flat dense round size=sm").classes("text-gray-400")

        async def poll_streaming_indicators() -> None:
            """Refresh just the is_streaming spinners in place, without
            touching the rest of the DOM — a full render_chat_list() rebuild
            on a timer was closing any dialog the user had open (e.g. edit
            title). Chats added/removed elsewhere are picked up next time
            something already calls render_chat_list().

            Stops the polling timer itself once nothing is streaming, so the
            app goes back to making zero background requests until something
            starts a stream again (see ``_wake_stream_poll``)."""
            if not streaming_spinners:
                stream_poll_timer.deactivate()
                return
            try:
                chats = await client.list_chats(state["project_id"])
            except Exception:  # noqa: BLE001
                return
            any_streaming = False
            for c in chats:
                if c.get("is_streaming"):
                    any_streaming = True
                spinner = streaming_spinners.get(c["id"])
                if spinner is not None:
                    spinner.set_visibility(bool(c.get("is_streaming")))
            if not any_streaming:
                stream_poll_timer.deactivate()

        def _wake_stream_poll() -> None:
            """Turn the polling timer back on — called whenever we know a
            stream just started (our own send()/reattach), so the row
            spinner elsewhere in the list stays in sync without polling at
            all while nothing is generating."""
            stream_poll_timer.activate()

        async def set_history(messages: list[dict]) -> None:
            # We're switching to a fixed, persisted message list — retire
            # whichever live-stream view (if any) was previously allowed to
            # render, so it can't write stray output into this new view, and
            # close out its network connection rather than leaving it open
            # until that generation finishes on its own.
            active_view["token"] = object()
            old_task = active_view["task"]
            active_view["task"] = None
            if old_task is not None and not old_task.done():
                old_task.cancel()
            history[:] = messages
            await render_history()
            update_context_usage()
            messages_area.scroll_to(percent=1.0)

        async def load_chat(chat_id: str) -> None:
            chat = await client.get_chat(chat_id)
            state["chat_id"] = chat["id"]
            state["workflow_id"] = chat.get("workflow_id")
            state["workflow_settings"] = chat.get("workflow_settings") or {}
            _store_chat_id(chat["id"])
            await set_history(chat["messages"])
            apply_chat_to_sidebar()
            await refresh_context_estimate()
            # Refresh the sidebar's highlight now, before reattaching — if
            # chat_id turns out to still be streaming, reattach_if_streaming
            # doesn't return until that response finishes, which would
            # otherwise leave the old chat highlighted the whole time.
            await render_chat_list()
            await reattach_if_streaming(chat_id)

        async def clear_chat() -> None:
            """Drop back to the no-chat-selected state (e.g. after deleting the
            last chat)."""
            state["chat_id"] = None
            state["workflow_id"] = None
            state["workflow_settings"] = {}
            state["extra_context_chars"] = 0
            _store_chat_id(None)
            await set_history([])
            apply_chat_to_sidebar()

        async def open_chat(chat_id: str) -> None:
            await load_chat(chat_id)
            await render_chat_list()

        async def new_chat() -> None:
            await refresh_models()
            if not workflows:
                ui.notify("No workflows available.", type="negative")
                return
            # A project selected in the left sidebar implies you want it as
            # context, so default to that workflow rather than a plain chat —
            # preferring whatever workflow/settings were last used for this
            # specific project, if any.
            project = (
                next((p for p in projects if p.get("id") == state["project_id"]), None)
                if state["project_id"]
                else None
            )
            project_default_id = project.get("default_workflow_id") if project else None
            if project_default_id and any(w["id"] == project_default_id for w in workflows):
                default_id = project_default_id
                stored_settings = project.get("default_workflow_settings") or {}
            elif state["project_id"] and any(w["id"] == "project_context" for w in workflows):
                default_id = "project_context"
                stored_settings = _stored_workflow_settings()
            else:
                default_id = "simple_chat"
                stored_settings = _stored_workflow_settings()
            workflow = next(w for w in workflows if w["id"] == default_id)
            defaults = _default_settings(
                workflow["settings_schema"],
                state["models"],
                _real_projects(),
                state["project_id"],
                stored_settings,
            )
            try:
                chat = await client.create_chat(
                    workflow_id=workflow["id"], workflow_settings=defaults
                )
            except Exception as exc:  # noqa: BLE001
                ui.notify(
                    f"Could not create chat: {_error_detail(exc)}",
                    type="negative",
                    multi_line=True,
                )
                return
            await load_chat(chat["id"])
            await render_chat_list()

        async def remove_chat(chat_id: str) -> None:
            try:
                await client.delete_chat(chat_id)
            except Exception as exc:  # noqa: BLE001
                ui.notify(f"Delete failed: {_error_detail(exc)}", type="negative")
                return
            if state["chat_id"] == chat_id:
                chats = await client.list_chats(state["project_id"])
                if chats:
                    await load_chat(chats[0]["id"])
                else:
                    await clear_chat()
            await render_chat_list()

        def edit_chat_title(chat_id: str, current_title: str) -> None:
            dialog = ui.dialog()
            with dialog, ui.card().classes("w-[360px]"):
                ui.label("Edit Chat Title").classes("text-base font-medium")
                title_input = ui.input("Title", value=current_title).classes("w-full")
                with ui.row().classes("w-full justify-end gap-2 mt-2"):
                    ui.button("Cancel", on_click=dialog.close).props("flat")
                    ui.button(
                        "Save",
                        on_click=lambda: save_chat_title(chat_id, title_input.value, dialog),
                    ).props("flat")
            dialog.open()
            # Focus the input when dialog opens
            ui.timer(0.1, lambda: title_input.focus(), once=True)

        async def save_chat_title(chat_id: str, new_title: str, dialog) -> None:
            new_title = (new_title or "").strip()
            if not new_title:
                ui.notify("Title cannot be empty.", type="warning")
                return
            try:
                updated = await client.update_chat(chat_id, title=new_title)
            except Exception as exc:  # noqa: BLE001
                ui.notify(f"Could not update title: {_error_detail(exc)}", type="negative")
                return
            dialog.close()
            # If this is the current chat, update the local state
            if state["chat_id"] == chat_id:
                # Update the title in the chat list by re-rendering
                pass
            await render_chat_list()
            ui.notify("Title updated.", type="positive")

        # --- Bottom: input -----------------------------------------------
        # Track whether a stream is currently in progress
        is_streaming = {"value": False}
        
        with ui.footer().classes("bg-[#2b323b] border-t border-[#585b5f] p-2"):
            with ui.row().classes("w-full max-w-5xl mx-auto items-end gap-2 no-wrap"):
                text_input = (
                    ui.textarea(placeholder="Type a message…")
                    .classes("flex-grow")
                    .props("outlined autogrow dense")
                )
                send_button = ui.button(icon="send").props("round")
                stop_button = ui.button(icon="stop").props("round unelevated")
                stop_button.set_visibility(False)  # Hidden by default
        text_input.on("keyup", lambda: update_context_usage())

        def make_stream_view(lazy: bool = False) -> dict:
            """Build a fresh "live assistant response" rendering scope: an
            open bubble with its own spinner, plus the on_delta/on_reasoning/
            on_tool_call/on_tool_result callbacks that drive it — shared
            between sending a new message and reattaching to one that's
            already streaming in the background (e.g. after switching chats
            and back mid-response).

            Every callback is guarded against ``active_view["token"]`` —
            captured once, up front, as this view's own token. If the user
            navigates away (which mints a new token via set_history) the
            callbacks silently stop touching the DOM, even if the background
            send()/reattach task they belong to keeps running — and even if
            the user later comes right back to this same chat, since that
            re-entry mints its own fresh token via a new reattach rather than
            reviving this one. Without this, a stale view can go on writing
            into whatever now-unrelated DOM the current view has built,
            producing duplicate bubbles.

            A fresh bubble is opened whenever the model still owes us output —
            initially, and again after every tool result — so the live view
            mirrors how a finished turn is persisted (one bubble per
            assistant/tool step, see render_history) and there's always
            visible feedback while waiting on the next round from the
            provider. Bubbles are created inside an explicit
            ``with messages_col:`` each time because these callbacks fire
            from async event handling, well after any earlier ``with`` block
            has closed — creating elements without that explicit context
            would silently attach them to the wrong container.

            With ``lazy=True`` no bubble is opened until the first event
            actually arrives — used for reattaching, where there may turn out
            to be nothing currently streaming at all.
            """
            my_token = active_view["token"]
            live: dict = {
                "bubble": None, "spinner": None, "md": None, "text": "",
                "reasoning_exp": None, "reasoning_md": None, "reasoning_text": "",
                "tool_column": None, "tool_spinner": None,
            }

            def open_assistant_bubble() -> None:
                with messages_col:
                    bubble = _message_bubble("Assistant", is_user=False)
                    with bubble:
                        with ui.column().classes("w-full gap-1"):
                            reasoning_exp = ui.expansion(
                                "Thinking…", icon="psychology"
                            ).classes("w-full text-gray-500 text-sm")
                            reasoning_exp.set_visibility(False)
                            with reasoning_exp:
                                reasoning_md = ui.markdown("").classes(
                                    "text-gray-500 text-sm italic break-words"
                                )
                            spinner = ui.spinner(size="sm")
                            md = ui.markdown("", extras=["fenced-code-blocks"]).classes(
                                "text-gray-200 break-words max-w-full"
                            )
                live.update(bubble=bubble, spinner=spinner, md=md, text="")
                messages_area.scroll_to(percent=1.0)

            def ensure_bubble() -> None:
                if live["bubble"] is None:
                    open_assistant_bubble()

            def hide_spinner() -> None:
                if live["spinner"] is not None:
                    live["spinner"].delete()
                    live["spinner"] = None

            def drop_bubble_if_empty() -> None:
                """A tool call can arrive before the assistant bubble opened
                for this round ever got any text or reasoning (the common
                case — the model calls a tool before saying anything). Drop
                the whole row rather than leave a blank "Assistant" bubble
                sitting above the tool bubble."""
                if live["bubble"] is not None and not live["text"] and not live["reasoning_text"]:
                    hide_spinner()
                    live["bubble"].row.delete()
                    live["bubble"] = None
                    live["md"] = None
                else:
                    hide_spinner()

            if not lazy:
                open_assistant_bubble()

            def on_delta(chunk: str) -> None:
                if active_view["token"] is not my_token:
                    return
                ensure_bubble()
                hide_spinner()
                live["text"] += chunk
                live["md"].set_content(live["text"])
                messages_area.scroll_to(percent=1.0)

            def on_reasoning(chunk: str) -> None:
                if active_view["token"] is not my_token:
                    return
                ensure_bubble()
                hide_spinner()
                exp = live["reasoning_exp"]
                if exp is not None and not exp.visible:
                    exp.set_visibility(True)
                    exp.open()
                live["reasoning_text"] += chunk
                live["reasoning_md"].set_content(live["reasoning_text"])
                messages_area.scroll_to(percent=1.0)

            def on_tool_call(name: str, arguments: dict) -> None:
                if active_view["token"] is not my_token:
                    return
                # The tool call gets its own bubble (mirroring render_history's
                # separate "Tool" bubble) rather than being inlined into the
                # assistant's text, and it carries its own spinner covering the
                # window where execute_tool() runs server-side with no
                # intermediate events — otherwise that wait looks like nothing
                # is happening.
                drop_bubble_if_empty()
                args_str = "\n".join(f"  {key}={value}" for key, value in arguments.items())
                with messages_col:
                    bubble = _message_bubble("Tool", is_user=False)
                    with bubble:
                        column = ui.column().classes("w-full gap-2")
                        with column:
                            with ui.expansion("Tool Call", icon="build").classes("w-full"):
                                ui.markdown(
                                    f"**Tool call:**\n```\n{name}\n{args_str}\n```",
                                    extras=["fenced-code-blocks"],
                                ).classes("text-gray-200 break-words max-w-full")
                            spinner = ui.spinner(size="sm")
                live.update(tool_column=column, tool_spinner=spinner)
                messages_area.scroll_to(percent=1.0)

            def on_tool_result(name: str, result: str) -> None:
                if active_view["token"] is not my_token:
                    return
                if live["tool_spinner"] is not None:
                    live["tool_spinner"].delete()
                    live["tool_spinner"] = None
                if live["tool_column"] is not None:
                    # Append the result into the same "Tool" bubble the call
                    # opened, right below it.
                    with live["tool_column"]:
                        with ui.expansion("Tool Result", icon="build").classes("w-full"):
                            ui.markdown(
                                f"**Result**\n\n```\n{result}\n```",
                                extras=["fenced-code-blocks"],
                            ).classes("text-gray-200 break-words max-w-full")
                    live["tool_column"] = None
                else:
                    # No tracked call bubble (e.g. reattached mid-execution) —
                    # fall back to a standalone result bubble.
                    with messages_col:
                        with _message_bubble("Tool", is_user=False):
                            with ui.expansion("Tool Result", icon="build").classes("w-full"):
                                ui.markdown(
                                    f"**Result**\n\n```\n{result}\n```",
                                    extras=["fenced-code-blocks"],
                                ).classes("text-gray-200 break-words max-w-full")
                # Generated images get their own bubble(s), with a Prompt
                # button when the result recorded one — mirroring
                # render_history's treatment of the persisted result.
                _render_image_bubbles(messages_col, _image_entries(result))
                # The model still owes a response to this result (more tool
                # calls, or the final answer) — open a fresh bubble+spinner so
                # the wait is never silent.
                open_assistant_bubble()

            return {
                "token": my_token,
                "on_delta": on_delta,
                "on_tool_call": on_tool_call,
                "on_tool_result": on_tool_result,
                "on_reasoning": on_reasoning,
                "hide_spinner": hide_spinner,
                "ensure_bubble": ensure_bubble,
            }

        async def send() -> None:
            content = (text_input.value or "").strip()
            if not content:
                return
            if not state["chat_id"]:
                ui.notify("Create a chat first.", type="warning")
                return

            own_chat_id = state["chat_id"]

            text_input.value = ""
            # Optimistically show the user's message.
            history.append({"role": "user", "content": content})
            await render_history()
            messages_area.scroll_to(percent=1.0)
            send_button.disable()
            is_streaming["value"] = True
            stop_button.set_visibility(True)
            _wake_stream_poll()
            # This is the chat's first message — lock the workflow choice
            # immediately rather than waiting for the round trip to finish.
            apply_chat_to_sidebar()

            view = make_stream_view()

            # Run as an explicit task (rather than a bare await) so a later
            # set_history() — from switching to another chat, even back to
            # this one — can cancel it outright and close its connection,
            # instead of leaving it open until the response finishes on its
            # own account. The generation itself is unaffected either way.
            task = asyncio.create_task(
                client.stream_message(
                    own_chat_id,
                    content,
                    view["on_delta"],
                    view["on_tool_call"],
                    view["on_tool_result"],
                    view["on_reasoning"],
                )
            )
            active_view["task"] = task

            updated: dict | None = None
            error: str | None = None
            try:
                updated = await task
            except asyncio.CancelledError:
                return  # superseded — its connection is already closing
            except Exception as exc:  # noqa: BLE001
                error = _error_detail(exc)

            if active_view["token"] is not view["token"]:
                # Superseded by a chat switch (possibly back to this very
                # chat, which mints its own fresh reattach view — see
                # make_stream_view). The response itself wasn't lost: it kept
                # generating server-side and is already persisted by now, so
                # just keep the sidebar (title/ordering) in sync.
                await render_chat_list()
                return

            is_streaming["value"] = False
            stop_button.set_visibility(False)
            send_button.enable()
            view["hide_spinner"]()
            if updated is not None:
                # Re-sync from the server (source of truth); this also replaces
                # the streaming bubble when render_history() clears the column.
                await set_history(updated["messages"])
                await render_chat_list()
            else:
                ui.notify(f"Chat failed: {error}", type="negative", multi_line=True)
                if history and history[-1]["role"] == "user":
                    history.pop()  # roll back the optimistic message
                await render_history()
                # If that was a failed first message, the workflow choice is
                # still open — undo the lock from the optimistic append above.
                apply_chat_to_sidebar()

        async def reattach_if_streaming(chat_id: str) -> None:
            """If ``chat_id`` has a response still generating in the
            background (we switched away mid-stream and back, or just
            reloaded the page), pick the live view back up instead of
            leaving the user looking at the not-yet-persisted history.

            Called right after set_history(), so ``active_view["token"]`` is
            already the fresh token for this chat view — captured by
            make_stream_view() below and reused for the user_message guard.
            """
            view = make_stream_view(lazy=True)
            send_button.disable()
            is_streaming["value"] = True
            stop_button.set_visibility(True)

            def on_user_message(question: str) -> None:
                if active_view["token"] is not view["token"]:
                    return
                # The turn that's still generating hasn't been persisted yet
                # (that only happens once it completes), so the question that
                # started it is otherwise invisible until then — show it
                # optimistically, same as send() does for a message we sent
                # ourselves. Also our first signal that a stream really is
                # active, so bring up the assistant bubble+spinner now rather
                # than waiting for the first delta.
                history.append({"role": "user", "content": question})
                with messages_col:
                    with _message_bubble("You", is_user=True):
                        ui.label(question).classes(
                            "text-gray-200 whitespace-pre-wrap break-words"
                        )
                view["ensure_bubble"]()
                _wake_stream_poll()

            # See send() — an explicit task so a later set_history() (e.g.
            # switching away again before this resolves) can cancel it and
            # close its connection right away.
            task = asyncio.create_task(
                client.reattach_stream(
                    chat_id,
                    view["on_delta"],
                    view["on_tool_call"],
                    view["on_tool_result"],
                    view["on_reasoning"],
                    on_user_message,
                )
            )
            active_view["task"] = task

            updated: dict | None = None
            error: str | None = None
            try:
                updated = await task
            except asyncio.CancelledError:
                return  # switched away again before this resolved
            except Exception as exc:  # noqa: BLE001
                error = _error_detail(exc)

            if active_view["token"] is not view["token"]:
                return  # switched away again before this resolved

            is_streaming["value"] = False
            stop_button.set_visibility(False)
            send_button.enable()
            if updated is None and error is None:
                return  # nothing was streaming — the bubble was never opened
            if updated is not None:
                await set_history(updated["messages"])
                await render_chat_list()
            else:
                ui.notify(f"Chat failed: {error}", type="negative", multi_line=True)
                await render_history()

        async def stop() -> None:
            """Stop the current stream and save the chat."""
            if not state["chat_id"]:
                return
            try:
                await client.stop_message(state["chat_id"])
                ui.notify("Stream stopped.", type="info")
            except Exception as exc:  # noqa: BLE001
                ui.notify(f"Could not stop stream: {_error_detail(exc)}", type="negative")

        send_button.on("click", send)
        stop_button.on("click", stop)
        text_input.on("keydown.enter", send)

        async def initial_load() -> None:
            await render_history()
            await load_projects()
            try:
                info = await client.get_provider()
                provider_label.text = f"{info['name']} · {info['base_url']}"
            except Exception:  # noqa: BLE001
                provider_label.text = "provider unavailable"
            await refresh_models()
            try:
                workflows[:] = await client.get_workflows()
            except Exception:  # noqa: BLE001
                workflows[:] = []

            # Restore the current chat across refreshes; otherwise open the
            # most recent one. If there's none at all, leave the empty state —
            # creating one now means picking a workflow (see the "new chat"
            # dialog), so there's nothing sensible to auto-create.
            loaded = False
            stored_id = _stored_chat_id()
            if stored_id:
                try:
                    await load_chat(stored_id)
                    loaded = True
                except Exception:  # noqa: BLE001 — chat was deleted / server restarted
                    loaded = False
            if not loaded:
                chats = await client.list_chats(state["project_id"])
                if chats:
                    await load_chat(chats[0]["id"])
                else:
                    await clear_chat()
            await render_chat_list()

        # NiceGUI must send the page within ~3s; defer slow provider/chat IO.
        ui.timer(0.0, initial_load, once=True)
        # Chats other than the one currently open can be streaming in the
        # background (see StreamState) — poll periodically so their spinner
        # (is_streaming) appears/disappears without needing a switch to that
        # chat and back. Only touches existing spinner elements in place
        # (see poll_streaming_indicators) — never rebuilds the list — so it
        # can't interrupt an open dialog. Starts off and is woken (see
        # _wake_stream_poll) only once something is actually streaming, then
        # switches itself back off once nothing is — so this makes zero
        # background requests the rest of the time.
        stream_poll_timer = ui.timer(3.0, poll_streaming_indicators, active=False)