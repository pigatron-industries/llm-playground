"""Central chat logic used by the chat routes.

This module owns the wire protocol (NDJSON events) and persistence, which are
the same regardless of which workflow is running. The workflow itself (looked
up from the chat) owns the model settings and the actual loop — see
``api.workflows``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from openai import APIConnectionError

from ..providers import StreamComplete, TextDelta, ReasoningDelta, ToolCallEvent, ToolResultEvent
from ..schemas import Message, SendMessageRequest
from ..store import get_store
from ..workflows import WorkflowContext, get_workflow


async def handle_send_message(chat_id: str, req: SendMessageRequest) -> AsyncIterator[str]:
    """Handle chat message submission and stream the assistant response."""
    store = get_store()
    chat = store.get(chat_id)
    if chat is None:
        raise KeyError(chat_id)

    workflow = get_workflow(chat.workflow_id)
    user_msg = Message(role="user", content=req.content)
    ctx = WorkflowContext(chat=chat, user_message=user_msg)

    produced: list[Message] = []
    try:
        async for event in workflow.run(ctx):
            if isinstance(event, TextDelta):
                yield json.dumps({"type": "delta", "content": event.content}) + "\n"
            if isinstance(event, ReasoningDelta):
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
        yield json.dumps({"type": "error", "detail": f"Could not reach provider: {exc}"}) + "\n"
        return
    except Exception as exc:  # noqa: BLE001 — any provider error mid-stream
        yield json.dumps({"type": "error", "detail": str(exc)}) + "\n"
        return

    store.add_message(chat_id, user_msg)
    for message in produced:
        store.add_message(chat_id, message)
    updated = store.get(chat_id)
    yield json.dumps({"type": "done", "chat": updated.model_dump(mode="json")}) + "\n"
