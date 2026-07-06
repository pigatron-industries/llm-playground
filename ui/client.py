"""Thin async HTTP client the UI uses to talk to the FastAPI backend.

The UI never touches the LLM provider or the chat store directly — it goes
through the API layer, keeping the two halves cleanly decoupled.
"""

from __future__ import annotations

import httpx

from api.config import get_self_api_url


def _client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=get_self_api_url(), timeout=timeout)


async def get_provider() -> dict:
    async with _client(10) as client:
        resp = await client.get("/provider")
        resp.raise_for_status()
        return resp.json()


async def get_models() -> list[str]:
    async with _client(30) as client:
        resp = await client.get("/models")
        resp.raise_for_status()
        return resp.json()["models"]


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


async def send_message(
    chat_id: str, content: str, model: str, temperature: float = 0.7
) -> dict:
    """Send a message; returns the full updated chat."""
    payload = {"content": content, "model": model, "temperature": temperature}
    async with _client(300) as client:
        resp = await client.post(f"/chats/{chat_id}/messages", json=payload)
        resp.raise_for_status()
        return resp.json()
