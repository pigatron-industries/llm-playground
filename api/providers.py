"""Modular LLM client.

Wraps the OpenAI async SDK against a configurable, OpenAI-compatible endpoint.
Because LM Studio, Ollama and OpenAI all speak the same protocol, the same
client works for every backend — only :class:`~api.config.ProviderConfig`
changes.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
from openai import AsyncOpenAI

from .config import ProviderConfig, get_active_provider
from .schemas import Message, ToolCall
from .tools import execute_tool


@dataclass(frozen=True)
class TextDelta:
    content: str


@dataclass(frozen=True)
class ReasoningDelta:
    content: str


@dataclass(frozen=True)
class ToolCallEvent:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResultEvent:
    name: str
    result: str


@dataclass(frozen=True)
class StreamComplete:
    """New assistant/tool messages produced during the tool loop."""

    messages: list[Message]


ChatEvent = TextDelta | ReasoningDelta | ToolCallEvent | ToolResultEvent | StreamComplete


def message_to_api(message: Message) -> dict[str, Any]:
    """Serialize a stored message for the OpenAI-compatible chat API."""
    payload: dict[str, Any] = {"role": message.role}
    if message.role == "tool":
        payload["tool_call_id"] = message.tool_call_id
        payload["content"] = message.content
        return payload
    if message.tool_calls:
        payload["content"] = message.content or None
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in message.tool_calls
        ]
        return payload
    payload["content"] = message.content
    return payload


class LLMClient:
    def __init__(self, config: ProviderConfig | None = None) -> None:
        self.config = config or get_active_provider()
        self._client = AsyncOpenAI(
            base_url=self.config.base_url,
            # Many local servers ignore the key but the SDK requires a non-empty one.
            api_key=self.config.api_key or "not-needed"
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
        tools: list[dict] | None = None,
    ) -> AsyncIterator[ChatEvent]:
        """Stream assistant text and tool-call events from the provider.

        When the model requests tools, each call is executed locally and the
        conversation continues until the model returns a final text answer.
        """
        api_messages = [message_to_api(message) for message in messages]
        produced: list[Message] = []

        while True:
            stream = await self._client.chat.completions.create(
                model=model,
                messages=api_messages,
                temperature=temperature,
                tools=tools or None,
                tool_choice="auto" if tools else None,
                stream=True,
            )

            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            tool_calls_acc: dict[int, dict[str, str]] = {}

            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                if delta.content:
                    content_parts.append(delta.content)
                    yield TextDelta(delta.content)

                reasoning_chunk = getattr(delta, "reasoning_content", None)
                if reasoning_chunk:
                    reasoning_parts.append(reasoning_chunk)
                    yield ReasoningDelta(reasoning_chunk)

                if delta.tool_calls:
                    for tool_call in delta.tool_calls:
                        index = tool_call.index
                        if index not in tool_calls_acc:
                            tool_calls_acc[index] = {"id": "", "name": "", "arguments": ""}
                        entry = tool_calls_acc[index]
                        if tool_call.id:
                            entry["id"] = tool_call.id
                        if tool_call.function.name:
                            entry["name"] = tool_call.function.name
                        if tool_call.function.arguments:
                            entry["arguments"] += tool_call.function.arguments

            if tool_calls_acc:
                ordered = [tool_calls_acc[index] for index in sorted(tool_calls_acc)]
                assistant_message = Message(
                    role="assistant",
                    content="".join(content_parts),
                    reasoning="".join(reasoning_parts),
                    tool_calls=[
                        ToolCall(id=entry["id"], name=entry["name"], arguments=entry["arguments"])
                        for entry in ordered
                    ],
                )
                produced.append(assistant_message)
                api_messages.append(message_to_api(assistant_message))

                for entry in ordered:
                    try:
                        arguments = json.loads(entry["arguments"] or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    yield ToolCallEvent(id=entry["id"], name=entry["name"], arguments=arguments)
                    result = await execute_tool(entry["name"], arguments)
                    yield ToolResultEvent(name=entry["name"], result=result)
                    tool_message = Message(
                        role="tool",
                        content=result,
                        tool_call_id=entry["id"],
                    )
                    produced.append(tool_message)
                    api_messages.append(message_to_api(tool_message))
                continue

            final_text = "".join(content_parts)
            final_reasoning = "".join(reasoning_parts)
            produced.append(Message(role="assistant", content=final_text, reasoning=final_reasoning))
            yield StreamComplete(messages=produced)
            return


# A single client is reused across requests; provider config is fixed at startup.
_client: LLMClient | None = None


def get_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
