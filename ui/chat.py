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

from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx
from nicegui import app, ui

from . import client
from .folder_picker import pick_folder


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


def _message_bubble(name: str, is_user: bool, timestamp: str | None = None):
    """Render a message row: participant name (+ time) on the left, bubble right.

    Returns the (empty) bubble element so the caller can fill it with the
    message text or a spinner.
    """
    with ui.row().classes("w-full items-start gap-3 no-wrap py-1"):
        with ui.column().classes("w-20 shrink-0 items-end gap-0 pt-2 select-none"):
            name_color = "text-indigo-500" if is_user else "text-gray-400"
            ui.label(name).classes(f"{name_color} text-xs font-medium")
            short, full = _format_time(timestamp)
            if short:
                ui.label(short).classes("text-[10px] text-gray-400").tooltip(full)
        bubble = ui.element("div").classes(
            "bg-gray-100 rounded-2xl px-4 py-2 grow min-w-0"
        )
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
    """
    return {
        field_name: _render_settings_field(
            field_name,
            field_schema,
            models,
            projects,
            values.get(field_name, field_schema.get("default")),
            on_change,
        )
        for field_name, field_schema in schema.get("properties", {}).items()
    }


def register_pages() -> None:
    @ui.page("/")
    async def chat_page() -> None:
        # Rendered conversation (mirror of the server-side chat) and the id of
        # the chat currently open.
        history: list[dict] = []
        workflows: list[dict] = []
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

        ui.query("body").classes("m-0")
        ui.colors(primary="#4f46e5")
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
        with ui.header().classes("items-center justify-between"):
            ui.label("LLM Test Harness").classes("text-lg font-semibold")
            provider_label = ui.label("").classes("text-sm opacity-80")

        # --- Left sidebar -------------------------------------------------
        with ui.left_drawer(bordered=True).classes("bg-gray-50").props("width=280"):
            with ui.row().classes("w-full items-center justify-between no-wrap"):
                ui.label("Projects").classes("text-sm font-medium text-gray-600")
                ui.button(icon="add", on_click=lambda: open_add_project_dialog()).props(
                    "flat dense round size=sm"
                ).tooltip("Add project")
            project_list = ui.column().classes("w-full gap-1 mt-2")

            ui.separator().classes("w-full my-3")

            with ui.row().classes("w-full items-center justify-between no-wrap"):
                ui.label("Chats").classes("text-sm font-medium text-gray-600")
                ui.button(icon="add", on_click=lambda: new_chat()).props(
                    "flat dense round size=sm"
                )
            chat_list = ui.column().classes("w-full gap-1")

        # --- Right sidebar: workflow settings ------------------------------
        with ui.right_drawer(bordered=True).classes("bg-gray-50").props("width=280"):

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
                ui.label("Workflow").classes("text-sm font-medium text-gray-600")
                ui.button(icon="refresh", on_click=lambda: refresh_model_options()).props(
                    "flat dense round size=sm"
                ).tooltip("Refresh models")
            # Locked once the chat has a first message (see PATCH /chats/{id});
            # the fields below it stay editable for the life of the chat.
            workflow_select = ui.select(options={}, label="Workflow").classes("w-full")
            settings_container = ui.column().classes("w-full gap-2 mt-1")

            ui.separator().classes("w-full my-2")

            # --- Context window ------------------------------------------
            ui.label("Context").classes("text-sm font-medium text-gray-600")
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
                    replace="text-xs " + ("text-red-500" if near else "text-gray-500")
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
                    row_bg = "bg-indigo-100" if current else "hover:bg-gray-200"
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

        def render_history() -> None:
            messages_col.clear()
            with messages_col:
                if not history:
                    placeholder = (
                        "Start the conversation below."
                        if state["chat_id"]
                        else 'No chat selected — click "+" next to Chats to create one.'
                    )
                    ui.label(placeholder).classes("text-gray-400 text-center w-full mt-8")
                for msg in history:
                    is_user = msg["role"] == "user"
                    if msg["role"] == "tool":
                        with _message_bubble("Tool", is_user=False, timestamp=msg.get("created_at")):
                            content = msg.get("content", "")
                            with ui.expansion("Tool Result", icon="build").classes("w-full"):
                                ui.markdown(
                                    f"**Result**\n\n```\n{content}\n```",
                                    extras=["fenced-code-blocks"],
                                ).classes("text-gray-800 break-words max-w-full")
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
                    ):
                        if is_user:
                            # Keep user input verbatim (no markdown interpretation).
                            ui.label(body_parts[0][0]).classes(
                                "text-gray-800 whitespace-pre-wrap break-words"
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
                                            ).classes("text-gray-800 break-words max-w-full")
                                    else:
                                        ui.markdown(
                                            part_text,
                                            extras=["fenced-code-blocks", "tables"],
                                        ).classes("text-gray-800 break-words max-w-full")

        async def render_chat_list() -> None:
            try:
                chats = await client.list_chats(state["project_id"])
            except Exception:  # noqa: BLE001
                return
            chat_list.clear()
            with chat_list:
                if not chats:
                    ui.label("No chats yet").classes("text-xs text-gray-400")
                for c in chats:
                    current = c["id"] == state["chat_id"]
                    row_bg = "bg-indigo-100" if current else "hover:bg-gray-200"
                    with ui.row().classes(
                        f"w-full items-center no-wrap gap-0 rounded {row_bg}"
                    ):
                        ui.button(
                            c["title"], on_click=lambda cid=c["id"]: open_chat(cid)
                        ).props("flat dense no-caps align=left").classes(
                            "flex-grow min-w-0 justify-start normal-case ellipsis"
                        )
                        ui.button(
                            icon="edit", on_click=lambda cid=c["id"], title=c["title"]: edit_chat_title(cid, title)
                        ).props("flat dense round size=sm").classes("text-gray-400").tooltip("Edit title")
                        ui.button(
                            icon="delete", on_click=lambda cid=c["id"]: remove_chat(cid)
                        ).props("flat dense round size=sm").classes("text-gray-400")

        def set_history(messages: list[dict]) -> None:
            history[:] = messages
            render_history()
            update_context_usage()
            messages_area.scroll_to(percent=1.0)

        async def load_chat(chat_id: str) -> None:
            chat = await client.get_chat(chat_id)
            state["chat_id"] = chat["id"]
            state["workflow_id"] = chat.get("workflow_id")
            state["workflow_settings"] = chat.get("workflow_settings") or {}
            _store_chat_id(chat["id"])
            set_history(chat["messages"])
            apply_chat_to_sidebar()
            await refresh_context_estimate()

        def clear_chat() -> None:
            """Drop back to the no-chat-selected state (e.g. after deleting the
            last chat)."""
            state["chat_id"] = None
            state["workflow_id"] = None
            state["workflow_settings"] = {}
            state["extra_context_chars"] = 0
            _store_chat_id(None)
            set_history([])
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
                    clear_chat()
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
        
        with ui.footer().classes("bg-white border-t p-2"):
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

        async def send() -> None:
            content = (text_input.value or "").strip()
            if not content:
                return
            if not state["chat_id"]:
                ui.notify("Create a chat first.", type="warning")
                return

            text_input.value = ""
            # Optimistically show the user's message.
            history.append({"role": "user", "content": content})
            render_history()
            messages_area.scroll_to(percent=1.0)
            send_button.disable()
            is_streaming["value"] = True
            stop_button.set_visibility(True)
            # This is the chat's first message — lock the workflow choice
            # immediately rather than waiting for the round trip to finish.
            apply_chat_to_sidebar()

            # Streaming render state. A fresh "Assistant" bubble (with its own
            # spinner) is opened whenever the model still owes us output —
            # initially, and again after every tool result — so the live view
            # mirrors how a finished turn is persisted (one bubble per
            # assistant/tool step, see render_history) and there's always
            # visible feedback while waiting on the next round from the
            # provider. Bubbles are created inside an explicit
            # ``with messages_col:`` each time because these callbacks fire
            # from async event handling, well after any earlier ``with``
            # block has closed — creating elements without that explicit
            # context would silently attach them to the wrong container.
            live: dict = {
                "bubble": None, "spinner": None, "md": None, "text": "",
                "reasoning_exp": None, "reasoning_md": None, "reasoning_text": "",
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
                                "text-gray-800 break-words max-w-full"
                            )
                live.update(bubble=bubble, spinner=spinner, md=md, text="")
                messages_area.scroll_to(percent=1.0)

            def hide_spinner() -> None:
                if live["spinner"] is not None:
                    live["spinner"].delete()
                    live["spinner"] = None

            open_assistant_bubble()

            def on_delta(chunk: str) -> None:
                hide_spinner()
                live["text"] += chunk
                live["md"].set_content(live["text"])
                messages_area.scroll_to(percent=1.0)

            def on_reasoning(chunk: str) -> None:
                hide_spinner()
                exp = live["reasoning_exp"]
                if exp is not None and not exp.visible:
                    exp.set_visibility(True)
                    exp.open()
                live["reasoning_text"] += chunk
                live["reasoning_md"].set_content(live["reasoning_text"])
                messages_area.scroll_to(percent=1.0)

            def on_tool_call(name: str, arguments: dict) -> None:
                hide_spinner()
                args_str = "\n".join(f"  {key}={value}" for key, value in arguments.items())
                live["text"] += f"**Tool call:**\n```\n{name}\n{args_str}\n```"
                live["md"].set_content(live["text"])
                messages_area.scroll_to(percent=1.0)

            def on_tool_result(name: str, result: str) -> None:
                with messages_col:
                    with _message_bubble("Tool", is_user=False):
                        with ui.expansion("Tool Result", icon="build").classes("w-full"):
                            ui.markdown(
                                f"**Result**\n\n```\n{result}\n```",
                                extras=["fenced-code-blocks"],
                            ).classes("text-gray-800 break-words max-w-full")
                # The model still owes a response to this result (more tool
                # calls, or the final answer) — open a fresh bubble+spinner so
                # the wait is never silent.
                open_assistant_bubble()

            updated: dict | None = None
            error: str | None = None
            try:
                updated = await client.stream_message(
                    state["chat_id"],
                    content,
                    on_delta,
                    on_tool_call,
                    on_tool_result,
                    on_reasoning
                )
            except Exception as exc:  # noqa: BLE001
                error = _error_detail(exc)
            finally:
                is_streaming["value"] = False
                stop_button.set_visibility(False)

            send_button.enable()
            hide_spinner()
            if updated is not None:
                # Re-sync from the server (source of truth); this also replaces
                # the streaming bubble when render_history() clears the column.
                set_history(updated["messages"])
                await render_chat_list()
            else:
                ui.notify(f"Chat failed: {error}", type="negative", multi_line=True)
                if history and history[-1]["role"] == "user":
                    history.pop()  # roll back the optimistic message
                render_history()
                # If that was a failed first message, the workflow choice is
                # still open — undo the lock from the optimistic append above.
                apply_chat_to_sidebar()

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
            render_history()
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
                    clear_chat()
            await render_chat_list()

        # NiceGUI must send the page within ~3s; defer slow provider/chat IO.
        ui.timer(0.0, initial_load, once=True)