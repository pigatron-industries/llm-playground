"""NiceGUI chat interface.

Full-screen layout:
  * left sidebar  — model selector, temperature, and the list of stored chats
  * center        — scrolling chat history
  * bottom        — input box + send button

Chats are persisted server-side (see ``api/store.py``). The UI remembers the
current chat id in browser storage, so a refresh reloads the full conversation
from the backend.
"""

from __future__ import annotations

from datetime import datetime

import httpx
from nicegui import app, ui

from . import client


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
    # try:
    local = datetime.fromisoformat(iso).astimezone()
    # except ValueError:
    #     return ("", "")
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


def register_pages() -> None:
    @ui.page("/")
    async def chat_page() -> None:
        # Rendered conversation (mirror of the server-side chat) and the id of
        # the chat currently open.
        history: list[dict] = []
        state: dict = {"chat_id": None}

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
        # --- Left sidebar: chats -----------------------------------------
        with ui.left_drawer(bordered=True).classes("bg-gray-50").props("width=280"):
            with ui.row().classes("w-full items-center justify-between no-wrap"):
                ui.label("Chats").classes("text-sm font-medium text-gray-600")
                ui.button("New", icon="add", on_click=lambda: new_chat()).props(
                    "flat dense no-caps"
                )
            chat_list = ui.column().classes("w-full gap-1")

        # --- Right sidebar: model settings -------------------------------
        with ui.right_drawer(bordered=True).classes("bg-gray-50").props("width=280"):

            async def refresh_models() -> None:
                try:
                    models = await client.get_models()
                except Exception as exc:  # noqa: BLE001
                    ui.notify(
                        f"Could not load models: {_error_detail(exc)}",
                        type="negative",
                        multi_line=True,
                    )
                    return
                model_select.options = models
                if models and model_select.value not in models:
                    model_select.value = models[0]
                model_select.update()

            with ui.row().classes("w-full items-center justify-between no-wrap"):
                ui.label("Model").classes("text-sm font-medium text-gray-600")
                ui.button(icon="refresh", on_click=refresh_models).props(
                    "flat dense round size=sm"
                ).tooltip("Refresh models")
            model_select = ui.select(
                options=[], with_input=True, label="Select a model"
            ).classes("w-full")

            temperature = ui.number(
                "Temperature", value=0.7, min=0, max=2, step=0.1, format="%.1f"
            ).classes("w-full")
            system_prompt = (
                ui.textarea("System prompt", placeholder="Optional instructions for the assistant")
                .classes("w-full")
                .props("outlined autogrow dense")
            )

        # --- Center: chat history ----------------------------------------
        messages_area = ui.scroll_area().classes("w-full flex-grow min-h-0")
        with messages_area:
            messages_col = ui.column().classes("w-full max-w-5xl mx-auto gap-2 p-4")

        def render_history() -> None:
            messages_col.clear()
            with messages_col:
                if not history:
                    ui.label("Start the conversation below.").classes(
                        "text-gray-400 text-center w-full mt-8"
                    )
                for msg in history:
                    is_user = msg["role"] == "user"
                    with _message_bubble(
                        "You" if is_user else "Assistant",
                        is_user,
                        msg.get("created_at"),
                    ):
                        if is_user:
                            # Keep user input verbatim (no markdown interpretation).
                            ui.label(msg["content"]).classes(
                                "text-gray-800 whitespace-pre-wrap break-words"
                            )
                        else:
                            # Render assistant replies as markdown.
                            ui.markdown(
                                msg["content"],
                                extras=["fenced-code-blocks", "tables"],
                            ).classes("text-gray-800 break-words max-w-full")

        async def render_chat_list() -> None:
            try:
                chats = await client.list_chats()
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
                            icon="delete", on_click=lambda cid=c["id"]: remove_chat(cid)
                        ).props("flat dense round size=sm").classes("text-gray-400")

        def set_history(messages: list[dict]) -> None:
            history[:] = messages
            render_history()
            messages_area.scroll_to(percent=1.0)

        async def load_chat(chat_id: str) -> None:
            chat = await client.get_chat(chat_id)
            state["chat_id"] = chat["id"]
            _store_chat_id(chat["id"])
            if chat.get("model") and chat["model"] in model_select.options:
                model_select.value = chat["model"]
            set_history(chat["messages"])

        async def open_chat(chat_id: str) -> None:
            await load_chat(chat_id)
            await render_chat_list()

        async def new_chat() -> None:
            chat = await client.create_chat(model=model_select.value)
            await load_chat(chat["id"])
            await render_chat_list()

        async def remove_chat(chat_id: str) -> None:
            try:
                await client.delete_chat(chat_id)
            except Exception as exc:  # noqa: BLE001
                ui.notify(f"Delete failed: {_error_detail(exc)}", type="negative")
                return
            if state["chat_id"] == chat_id:
                chats = await client.list_chats()
                if chats:
                    await load_chat(chats[0]["id"])
                else:
                    fresh = await client.create_chat(model=model_select.value)
                    await load_chat(fresh["id"])
            await render_chat_list()

        # --- Bottom: input -----------------------------------------------
        with ui.footer().classes("bg-white border-t p-2"):
            with ui.row().classes("w-full max-w-5xl mx-auto items-end gap-2 no-wrap"):
                text_input = (
                    ui.textarea(placeholder="Type a message…")
                    .classes("flex-grow")
                    .props("outlined autogrow dense")
                )
                send_button = ui.button(icon="send").props("round")

        async def send() -> None:
            content = (text_input.value or "").strip()
            if not content:
                return
            if not model_select.value:
                ui.notify("Select a model first.", type="warning")
                return
            if not state["chat_id"]:
                chat = await client.create_chat(model=model_select.value)
                state["chat_id"] = chat["id"]
                _store_chat_id(chat["id"])

            text_input.value = ""
            # Optimistically show the user's message, then a streaming bubble.
            history.append({"role": "user", "content": content})
            render_history()
            with messages_col:
                with _message_bubble("Assistant", is_user=False):
                    spinner = ui.spinner(size="sm")
                    reply_md = ui.markdown("").classes(
                        "text-gray-800 break-words max-w-full"
                    )
            messages_area.scroll_to(percent=1.0)
            send_button.disable()

            acc = {"text": "", "started": False}

            def on_delta(chunk: str) -> None:
                if not acc["started"]:
                    spinner.delete()  # first token arrived
                    acc["started"] = True
                acc["text"] += chunk
                reply_md.set_content(acc["text"])
                messages_area.scroll_to(percent=1.0)

            updated: dict | None = None
            error: str | None = None
            try:
                updated = await client.stream_message(
                    state["chat_id"],
                    content,
                    model_select.value,
                    float(temperature.value or 0.7),
                    (system_prompt.value or "").strip() or None,
                    on_delta,
                )
            except Exception as exc:  # noqa: BLE001
                error = _error_detail(exc)

            send_button.enable()
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

        send_button.on("click", send)
        text_input.on("keydown.enter", send)

        # --- Initial load -------------------------------------------------
        render_history()
        try:
            info = await client.get_provider()
            provider_label.text = f"{info['name']} · {info['base_url']}"
        except Exception:  # noqa: BLE001
            provider_label.text = "provider unavailable"
        await refresh_models()

        # Restore the current chat across refreshes; otherwise open the most
        # recent, or create a fresh one.
        loaded = False
        stored_id = _stored_chat_id()
        if stored_id:
            try:
                await load_chat(stored_id)
                loaded = True
            except Exception:  # noqa: BLE001 — chat was deleted / server restarted
                loaded = False
        if not loaded:
            chats = await client.list_chats()
            if chats:
                await load_chat(chats[0]["id"])
            else:
                fresh = await client.create_chat(model=model_select.value)
                await load_chat(fresh["id"])
        await render_chat_list()
