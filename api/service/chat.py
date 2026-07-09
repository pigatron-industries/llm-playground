"""Central chat logic used by the chat routes."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from openai import APIConnectionError

from ..providers import StreamComplete, TextDelta, ToolCallEvent, ToolResultEvent, get_client
from ..schemas import Message, SendMessageRequest
from ..store import get_store
from ..tools import DEFAULT_TOOLS


async def handle_send_message(chat_id: str, req: SendMessageRequest) -> AsyncIterator[str]:
    """Handle chat message submission and streaming the assistant response."""
    store = get_store()
    chat = store.get(chat_id)
    if chat is None:
        raise KeyError(chat_id)

    user_msg = Message(role="user", content=req.content)
    conversation: list[Message] = []
    if req.system_prompt:
        conversation.append(Message(role="system", content=req.system_prompt))
    conversation.extend(chat.messages)
    conversation.append(user_msg)
    client = get_client()
    tools = req.tools if req.tools is not None else DEFAULT_TOOLS

    produced: list[Message] = []
    try:
        async for event in client.chat_stream(
            model=req.model,
            messages=conversation,
            temperature=req.temperature,
            tools=tools,
        ):
            if isinstance(event, TextDelta):
                yield json.dumps({"type": "delta", "content": event.content}) + "\n"
            elif isinstance(event, ToolCallEvent):
                yield (
                    json.dumps(
                        {
                            "type": "tool_call",
                            "name": event.name,
                            "arguments": event.arguments,
                        }
                    )
                    + "\n"
                )
            elif isinstance(event, ToolResultEvent):
                yield (
                    json.dumps(
                        {
                            "type": "tool_result",
                            "name": event.name,
                            "result": event.result,
                        }
                    )
                    + "\n"
                )
            elif isinstance(event, StreamComplete):
                produced = event.messages
    except APIConnectionError as exc:
        detail = f"Could not reach provider at {client.config.base_url}: {exc}"
        yield json.dumps({"type": "error", "detail": detail}) + "\n"
        return
    except Exception as exc:  # noqa: BLE001 — any provider error mid-stream
        yield json.dumps({"type": "error", "detail": str(exc)}) + "\n"
        return

    store.add_message(chat_id, user_msg)
    for message in produced:
        store.add_message(chat_id, message)
    store.set_model(chat_id, req.model)
    updated = store.get(chat_id)
    yield json.dumps({"type": "done", "chat": updated.model_dump(mode="json")}) + "\n"
