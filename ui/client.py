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
    async with _client(30) as client:
        resp = await client.get("/models")
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


async def delete_project(project_id: str) -> None:
    async with _client(10) as client:
        resp = await client.delete(f"/projects/{project_id}")
        resp.raise_for_status()


# --- Stored chats ----------------------------------------------------------


async def list_chats() -> list[dict]:
    async with _client(10) as client:
        resp = await client.get("/chats")
        resp.raise_for_status()
        return resp.json()


async def create_chat(model: str | None = None) -> dict:
    async with _client(10) as client:
        resp = await client.post("/chats", json={"model": model})
        resp.raise_for_status()
        return resp.json()


async def get_chat(chat_id: str) -> dict:
    async with _client(10) as client:
        resp = await client.get(f"/chats/{chat_id}")
        resp.raise_for_status()
        return resp.json()


async def delete_chat(chat_id: str) -> None:
    async with _client(10) as client:
        resp = await client.delete(f"/chats/{chat_id}")
        resp.raise_for_status()


async def stream_message(
    chat_id: str,
    content: str,
    model: str,
    temperature: float,
    system_prompt: str | None,
    on_delta: Callable[[str], None],
) -> dict:
    """Stream a message reply, calling ``on_delta`` per chunk.

    Returns the full updated chat (from the final ``done`` event). Raises on a
    provider error (surfaced as an ``error`` event) or an HTTP error.
    """
    payload = {
        "content": content,
        "model": model,
        "temperature": temperature,
        "system_prompt": system_prompt,
    }
    final_chat: dict | None = None
    async with _client(300) as client:
        async with client.stream(
            "POST", f"/chats/{chat_id}/messages", json=payload
        ) as resp:
            if resp.status_code >= 400:
                await resp.aread()
                resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                event = json.loads(line)
                kind = event.get("type")
                if kind == "delta":
                    on_delta(event["content"])
                elif kind == "error":
                    raise RuntimeError(event["detail"])
                elif kind == "done":
                    final_chat = event["chat"]
    if final_chat is None:
        raise RuntimeError("Stream ended without a completion event")
    return final_chat
