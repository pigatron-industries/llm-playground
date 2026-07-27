"""Central chat logic used by the chat routes.

This module owns the wire protocol (NDJSON events) and persistence, which are
the same regardless of which workflow is running. The workflow itself (looked
up from the chat) owns the model settings and the actual loop — see
``api.workflows``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from openai import APIConnectionError

from ..providers import StreamComplete, TextDelta, ReasoningDelta, ToolCallEvent, ToolResultEvent
from ..schemas import Message, SendMessageRequest
from ..store import get_store
from ..workflows import WorkflowContext, get_workflow

log = logging.getLogger("llm_harness.chat")

# Global registry of active streams: chat_id -> asyncio.Event (set when stop is requested)
_active_streams: dict[str, asyncio.Event] = {}


def register_stream(chat_id: str) -> asyncio.Event:
    """Register a stream and return its cancellation event."""
    cancel_event = asyncio.Event()
    _active_streams[chat_id] = cancel_event
    return cancel_event


def unregister_stream(chat_id: str) -> None:
    """Unregister a stream (called after streaming completes)."""
    _active_streams.pop(chat_id, None)


def is_stream_cancelled(chat_id: str) -> bool:
    """Check if a stream has been cancelled."""
    cancel_event = _active_streams.get(chat_id)
    return cancel_event is not None and cancel_event.is_set()


def request_stream_stop(chat_id: str) -> bool:
    """Request a stream to stop. Returns True if the stream was found and stopping."""
    cancel_event = _active_streams.get(chat_id)
    if cancel_event is not None:
        cancel_event.set()
        return True
    return False


async def handle_send_message(chat_id: str, req: SendMessageRequest) -> AsyncIterator[str]:
    """Handle chat message submission and stream the assistant response."""
    store = get_store()
    chat = store.get(chat_id)
    if chat is None:
        raise KeyError(chat_id)

    workflow = get_workflow(chat.workflow_id)
    user_msg = Message(role="user", content=req.content)
    ctx = WorkflowContext(chat=chat, user_message=user_msg)

    # Register this stream for potential cancellation
    cancel_event = register_stream(chat_id)

    produced: list[Message] = []
    stopped = False
    try:
        async for event in workflow.run(ctx):
            # Check if stream has been cancelled
            if cancel_event.is_set():
                log.info("Stream cancelled for chat %s", chat_id)
                stopped = True
                break

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
        log.exception("Could not reach provider for chat %s", chat_id)
        yield json.dumps({"type": "error", "detail": f"Could not reach provider: {exc}"}) + "\n"
        return
    except Exception as exc:  # noqa: BLE001 — any provider error mid-stream
        log.exception("Provider error mid-stream for chat %s", chat_id)
        yield json.dumps({"type": "error", "detail": str(exc)}) + "\n"
        return
    finally:
        unregister_stream(chat_id)

    store.add_message(chat_id, user_msg)
    for message in produced:
        store.add_message(chat_id, message)
    updated = store.get(chat_id)
    
    # If stream was stopped by user, send a "stopped" event instead of "done"
    if stopped:
        yield json.dumps({"type": "stopped", "chat": updated.model_dump(mode="json")}) + "\n"
    else:
        yield json.dumps({"type": "done", "chat": updated.model_dump(mode="json")}) + "\n"
