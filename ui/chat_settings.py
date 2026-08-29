from __future__ import annotations

import asyncio
from typing import Any, Callable

from nicegui import ui

from . import client
from .chat_utils import _error_detail
from api.config import DEFAULT_MODEL_BASES


def _default_settings(
    schema: dict,
    models: list[str],
    projects: list[dict] | None = None,
    selected_project_id: str | None = None,
    stored_settings: dict | None = None,
) -> dict:
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
    base_label = base_schema.get("title") or "Image base"
    model_label = model_schema.get("title") or "Image model"
    base_description = base_schema.get("description")
    model_description = model_schema.get("description")

    base_value = base_value if base_value in DEFAULT_MODEL_BASES else DEFAULT_MODEL_BASES[0]
    base_select = ui.select(
        options=list(DEFAULT_MODEL_BASES), value=base_value, label=base_label
    ).classes("w-full")
    if base_description:
        base_select.tooltip(base_description)

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
        await _load_models(e.value, keep=None, save=True)

    base_select.on_value_change(_on_base_change)

    try:
        asyncio.get_running_loop().create_task(
            _load_models(base_value, keep=model_value, save=False)
        )
    except RuntimeError:
        pass

    return (lambda: base_select.value or "", lambda: model_select.value or last_known_model or "")


def _render_settings_form(
    schema: dict,
    models: list[str],
    projects: list[dict],
    values: dict,
    on_change: Callable[[], None] | None = None,
) -> dict[str, Callable[[], Any]]:
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
