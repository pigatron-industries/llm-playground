"""Modular LLM client.

Wraps the OpenAI async SDK against a configurable, OpenAI-compatible endpoint.
Because LM Studio, Ollama and OpenAI all speak the same protocol, the same
client works for every backend — only :class:`~api.config.ProviderConfig`
changes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

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
