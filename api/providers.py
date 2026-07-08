"""Modular LLM client.

Wraps the OpenAI async SDK against a configurable, OpenAI-compatible endpoint.
Because LM Studio, Ollama and OpenAI all speak the same protocol, the same
client works for every backend — only :class:`~api.config.ProviderConfig`
changes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from openai import AsyncOpenAI

from .config import ProviderConfig, get_active_provider
from .schemas import Message


class LLMClient:
    def __init__(self, config: ProviderConfig | None = None) -> None:
        self.config = config or get_active_provider()
        self._client = AsyncOpenAI(
            base_url=self.config.base_url,
            # Many local servers ignore the key but the SDK requires a non-empty one.
            api_key=self.config.api_key or "not-needed",
        )

    async def list_models(self) -> list[str]:
        resp = await self._client.models.list()
        return sorted(m.id for m in resp.data)

    async def model_context_lengths(self) -> dict[str, int]:
        """Best-effort map of model id -> context window (tokens).

        The OpenAI ``/v1/models`` surface carries no context-window size, so we
        probe provider-native endpoints we recognise. LM Studio exposes this on
        its REST API (``/api/v0/models``), a sibling of the ``/v1`` OpenAI
        surface. Returns ``{}`` when nothing usable is available — callers treat
        missing entries as "unknown".
        """
        base = self.config.base_url.rstrip("/")
        if not base.endswith("/v1"):
            return {}
        native = f"{base[:-3]}/api/v0/models"  # .../v1 -> .../api/v0/models
        headers = {}
        if self.config.api_key and self.config.api_key != "not-needed":
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        try:
            async with httpx.AsyncClient(timeout=5) as http:
                resp = await http.get(native, headers=headers)
                resp.raise_for_status()
                entries = resp.json().get("data", [])
        except Exception:  # noqa: BLE001 — provider may not expose this endpoint
            return {}
        lengths: dict[str, int] = {}
        for entry in entries:
            model_id = entry.get("id")
            # Prefer the loaded window (what the model will actually accept)
            # over the max the file supports.
            ctx = entry.get("loaded_context_length") or entry.get("max_context_length")
            if isinstance(model_id, str) and isinstance(ctx, int) and ctx > 0:
                lengths[model_id] = ctx
        return lengths

    async def chat_stream(
        self,
        model: str,
        messages: list[Message],
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """Yield assistant content deltas as they stream from the provider."""
        stream = await self._client.chat.completions.create(
            model=model,
            # Only role/content — the API rejects extra fields like created_at.
            messages=[{"role": m.role, "content": m.content} for m in messages],
            temperature=temperature,
            stream=True,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta


# A single client is reused across requests; provider config is fixed at startup.
_client: LLMClient | None = None


def get_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
