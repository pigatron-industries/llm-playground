"""Thin async HTTP client the UI uses to talk to the FastAPI backend.

The UI never touches the LLM provider or the chat store directly — it goes
through the API layer, keeping the two halves cleanly decoupled.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx

from api.config import get_self_api_url


def _client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=get_self_api_url(), timeout=timeout)


async def get_provider() -> dict:
    async with _client(10) as client:
        resp = await client.get("/provider")
        resp.raise_for_status()
        return resp.json()


async def get_models() -> dict:
    """Return the models payload: ``{"models": [...], "context_lengths": {...}}``."""
    async with _client(5) as client:
        resp = await client.get("/models")
        resp.raise_for_status()
        return resp.json()


async def get_workflows() -> list[dict]:
    """Return ``[{"id", "name", "description", "settings_schema"}, ...]``."""
    async with _client(10) as client:
        resp = await client.get("/workflows")
        resp.raise_for_status()
        return resp.json()


async def get_image_models(base: str | None = None) -> dict:
    """Return ``{"base": ..., "models": [{"name", "base"}, ...]}`` — the
    image-generation models on the external image API for a given base model
    (via the backend's ``/image/models`` proxy)."""
    params = {}
    if base is not None:
        params["base"] = base
    async with _client(20) as client:
        resp = await client.get("/image/models", params=params or None)
        resp.raise_for_status()
        return resp.json()


async def get_image_loras(base: str | None = None) -> dict:
    """Return ``{"base": ..., "loras": [...]}`` — the LoRAs available on the
    external image API for a given base (via the backend's ``/image/loras``
    proxy)."""
    params = {}
    if base is not None:
        params["base"] = base
    async with _client(20) as client:
        resp = await client.get("/image/loras", params=params or None)
        resp.raise_for_status()
        return resp.json()


# --- Projects --------------------------------------------------------------


async def list_projects() -> list[dict]:
    async with _client(10) as client:
        resp = await client.get("/projects")
        resp.raise_for_status()
        return resp.json()


async def create_project(name: str, path: str) -> dict:
    async with _client(10) as client:
        resp = await client.post("/projects", json={"name": name, "path": path})
        resp.raise_for_status()
        return resp.json()


async def update_project(
    project_id: str,
    name: str | None = None,
    path: str | None = None,
    default_workflow_id: str | None = None,
    default_workflow_settings: dict | None = None,
) -> dict:
    payload: dict = {}
    if name is not None:
        payload["name"] = name
    if path is not None:
        payload["path"] = path
    if default_workflow_id is not None:
        payload["default_workflow_id"] = default_workflow_id
    if default_workflow_settings is not None:
        payload["default_workflow_settings"] = default_workflow_settings
    async with _client(10) as client:
        resp = await client.patch(f"/projects/{project_id}", json=payload)
        resp.raise_for_status()
        return resp.json()


async def delete_project(project_id: str) -> None:
    async with _client(10) as client:
        resp = await client.delete(f"/projects/{project_id}")
        resp.raise_for_status()


# --- Stored chats ----------------------------------------------------------


async def list_chats(project_id: str | None = None) -> list[dict]:
    async with _client(10) as client:
        params = {}
        if project_id is not None:
            params["project_id"] = project_id
        resp = await client.get("/chats", params=params if params else None)
        resp.raise_for_status()
        return resp.json()


async def create_chat(
    title: str | None = None,
    workflow_id: str | None = None,
    workflow_settings: dict | None = None,
) -> dict:
    async with _client(10) as client:
        resp = await client.post(
            "/chats",
            json={
                "title": title,
                "workflow_id": workflow_id,
                "workflow_settings": workflow_settings or {},
            },
        )
        resp.raise_for_status()
        return resp.json()


async def get_chat(chat_id: str) -> dict:
    async with _client(10) as client:
        resp = await client.get(f"/chats/{chat_id}")
        resp.raise_for_status()
        return resp.json()


async def get_context_estimate(chat_id: str) -> dict:
    """Return ``{"extra_context_chars": int}`` — the chat's workflow's
    best-effort estimate of extra context beyond the visible history."""
    async with _client(10) as client:
        resp = await client.get(f"/chats/{chat_id}/context_estimate")
        resp.raise_for_status()
        return resp.json()


async def update_chat(
    chat_id: str,
    title: str | None = None,
    workflow_id: str | None = None,
    workflow_settings: dict | None = None,
) -> dict:
    """Update a chat's title, workflow and/or settings.

    ``workflow_id`` is only accepted server-side while the chat has no
    messages yet; pass it together with fresh ``workflow_settings`` for the
    new workflow's schema.
    ``title`` can be updated at any time.
    """
    payload: dict = {}
    if title is not None:
        payload["title"] = title
    if workflow_id is not None:
        payload["workflow_id"] = workflow_id
    if workflow_settings is not None:
        payload["workflow_settings"] = workflow_settings
    async with _client(10) as client:
        resp = await client.patch(f"/chats/{chat_id}", json=payload)
        resp.raise_for_status()
        return resp.json()


async def delete_chat(chat_id: str) -> None:
    async with _client(10) as client:
        resp = await client.delete(f"/chats/{chat_id}")
        resp.raise_for_status()


async def _consume_ndjson(
    resp: httpx.Response,
    on_delta: Callable[[str], None],
    on_tool_call: Callable[[str, dict], None] | None,
    on_tool_result: Callable[[str, str], None] | None,
    on_reasoning: Callable[[str], None] | None,
    on_user_message: Callable[[str], None] | None = None,
) -> dict | None:
    """Drive callbacks from an open NDJSON response, returning the final
    persisted chat once a ``done``/``stopped`` event arrives (or ``None`` if
    the stream ended without one)."""
    final_chat: dict | None = None
    async for line in resp.aiter_lines():
        if not line.strip():
            continue
        event = json.loads(line)
        kind = event.get("type")
        if kind == "delta":
            on_delta(event["content"])
        elif kind == "reasoning" and on_reasoning is not None:
            on_reasoning(event["content"])
        elif kind == "tool_call" and on_tool_call is not None:
            on_tool_call(event["name"], event.get("arguments", {}))
        elif kind == "tool_result" and on_tool_result is not None:
            on_tool_result(event["name"], event["result"])
        elif kind == "user_message" and on_user_message is not None:
            on_user_message(event["content"])
        elif kind == "error":
            raise RuntimeError(event["detail"])
        elif kind in ("done", "stopped"):
            final_chat = event["chat"]
    return final_chat


async def stream_message(
    chat_id: str,
    content: str,
    on_delta: Callable[[str], None],
    on_tool_call: Callable[[str, dict], None] | None = None,
    on_tool_result: Callable[[str, str], None] | None = None,
    on_reasoning: Callable[[str], None] | None = None,
) -> dict:
    """Stream a message reply, calling ``on_delta`` per chunk (and
    ``on_reasoning`` per chunk of the model's reasoning trace, if the
    provider/model emits one — most don't, so it may never fire).

    Generation runs server-side independent of this connection — if it
    drops (e.g. the caller navigates away), the response keeps generating
    and can be picked back up with ``reattach_stream``.
    """
    payload = {"content": content}
    async with _client(600) as client:
        async with client.stream(
            "POST", f"/chats/{chat_id}/messages", json=payload
        ) as resp:
            if resp.status_code >= 400:
                await resp.aread()
                resp.raise_for_status()
            final_chat = await _consume_ndjson(
                resp, on_delta, on_tool_call, on_tool_result, on_reasoning
            )
    if final_chat is None:
        raise RuntimeError("Stream ended without a completion event")
    return final_chat


async def reattach_stream(
    chat_id: str,
    on_delta: Callable[[str], None],
    on_tool_call: Callable[[str, dict], None] | None = None,
    on_tool_result: Callable[[str, str], None] | None = None,
    on_reasoning: Callable[[str], None] | None = None,
    on_user_message: Callable[[str], None] | None = None,
) -> dict | None:
    """Reattach to a message stream already in progress for ``chat_id`` —
    e.g. the UI switched to another chat and back while a response was
    still generating. Replays whatever's already been produced through the
    callbacks (``on_user_message`` fires first, with the not-yet-persisted
    question that started this turn), then keeps streaming live.

    Returns the final chat once complete, or ``None`` if nothing is
    currently streaming for this chat (nothing to reattach to).
    """
    async with _client(600) as client:
        async with client.stream("GET", f"/chats/{chat_id}/stream") as resp:
            if resp.status_code == 404:
                await resp.aread()
                return None
            if resp.status_code >= 400:
                await resp.aread()
                resp.raise_for_status()
            final_chat = await _consume_ndjson(
                resp, on_delta, on_tool_call, on_tool_result, on_reasoning, on_user_message
            )
    if final_chat is None:
        raise RuntimeError("Stream ended without a completion event")
    return final_chat


async def stop_message(chat_id: str) -> dict:
    """Request to stop an in-flight message stream."""
    async with _client(10) as client:
        resp = await client.post(f"/chats/{chat_id}/stop")
        resp.raise_for_status()
        return resp.json()


async def get_chat_state(chat_id: str) -> dict:
    """Return the current state dict for a chat's workflow (e.g. map, status).
    Returns empty dict for workflows that don't support state output."""
    async with _client(10) as client:
        resp = await client.get(f"/chats/{chat_id}/state")
        resp.raise_for_status()
        return resp.json()


async def delete_message(chat_id: str, index: int) -> dict:
    """Delete a message from a chat's history by its index."""
    async with _client(10) as client:
        resp = await client.delete(f"/chats/{chat_id}/messages/{index}")
        resp.raise_for_status()
        return resp.json()
