"""Central chat logic used by the chat routes.

This module owns the wire protocol (NDJSON events) and persistence, which are
the same regardless of which workflow is running. The workflow itself (looked
up from the chat) owns the model settings and the actual loop — see
``api.workflows``.

Generation runs in a background task (see ``StreamState``/``start_stream``)
independent of any single HTTP connection, so a client disconnecting (e.g.
the UI navigating to another chat) doesn't cancel the in-flight response —
any client can reattach via ``get_active_stream`` and replay/continue
watching the same buffered events.
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


class StreamState:
    """Buffers NDJSON event lines produced by a background generation task
    so any number of HTTP requests can subscribe/reattach and replay from
    wherever they left off, independent of the task's own lifetime."""

    def __init__(self) -> None:
        self.cancel_event = asyncio.Event()
        self.condition = asyncio.Condition()
        self.events: list[str] = []
        self.done = False
        self.task: asyncio.Task | None = None

    async def append(self, line: str) -> None:
        async with self.condition:
            self.events.append(line)
            self.condition.notify_all()

    async def finish(self) -> None:
        async with self.condition:
            self.done = True
            self.condition.notify_all()

    async def subscribe(self) -> AsyncIterator[str]:
        """Yield every event from the start, then keep waiting for new ones
        until the stream finishes. Safe to call more than once (e.g. the
        original requester plus a later reattach) — each subscriber tracks
        its own read position over the same shared buffer."""
        idx = 0
        while True:
            async with self.condition:
                while idx >= len(self.events) and not self.done:
                    await self.condition.wait()
                pending = self.events[idx:]
                idx = len(self.events)
                finished = self.done
            for line in pending:
                yield line
            if finished:
                return


# Global registry of active streams: chat_id -> StreamState
_active_streams: dict[str, StreamState] = {}


def get_active_stream(chat_id: str) -> StreamState | None:
    """Return the in-progress stream for a chat, if any — used to reattach
    a UI that switched away and back while the response was still generating."""
    return _active_streams.get(chat_id)


def list_active_stream_ids() -> set[str]:
    """Ids of every chat currently generating in the background — used to
    flag them in the chat list (e.g. a spinner next to the title) even while
    the user is looking at a different chat."""
    return set(_active_streams.keys())


def request_stream_stop(chat_id: str) -> bool:
    """Request a stream to stop. Returns True if the stream was found and stopping."""
    state = _active_streams.get(chat_id)
    if state is not None:
        state.cancel_event.set()
        return True
    return False


def start_stream(chat_id: str, req: SendMessageRequest) -> StreamState:
    """Start generating the assistant reply in a background task and return
    immediately. The task keeps running (and persists its result) even if
    every subscriber disconnects — callers consume it via ``state.subscribe()``."""
    state = StreamState()
    # The user's message isn't persisted until the turn completes (see
    # _run_stream), so a client reattaching mid-stream has no other way to
    # learn what was actually asked — seed it as the first buffered event.
    state.events.append(json.dumps({"type": "user_message", "content": req.content}) + "\n")
    _active_streams[chat_id] = state
    state.task = asyncio.create_task(_run_stream(chat_id, req, state))
    return state


async def _run_stream(chat_id: str, req: SendMessageRequest, state: StreamState) -> None:
    """Run the workflow loop to completion, appending wire-protocol events to
    ``state`` as they're produced, then persist and unregister."""
    store = get_store()
    chat = store.get(chat_id)
    if chat is None:
        await state.append(json.dumps({"type": "error", "detail": "Chat not found"}) + "\n")
        await state.finish()
        _active_streams.pop(chat_id, None)
        return

    workflow = get_workflow(chat.workflow_id)
    user_msg = Message(role="user", content=req.content)
    ctx = WorkflowContext(chat=chat, user_message=user_msg)

    produced: list[Message] = []
    stopped = False
    failed = False
    try:
        async for event in workflow.run(ctx):
            if state.cancel_event.is_set():
                log.info("Stream cancelled for chat %s", chat_id)
                stopped = True
                break

            if isinstance(event, TextDelta):
                await state.append(json.dumps({"type": "delta", "content": event.content}) + "\n")
            if isinstance(event, ReasoningDelta):
                await state.append(json.dumps({"type": "delta", "content": event.content}) + "\n")
            elif isinstance(event, ToolCallEvent):
                await state.append(
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
                await state.append(
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
        await state.append(
            json.dumps({"type": "error", "detail": f"Could not reach provider: {exc}"}) + "\n"
        )
        failed = True
    except Exception as exc:  # noqa: BLE001 — any provider error mid-stream
        log.exception("Provider error mid-stream for chat %s", chat_id)
        await state.append(json.dumps({"type": "error", "detail": str(exc)}) + "\n")
        failed = True

    if not failed:
        store.add_message(chat_id, user_msg)
        for message in produced:
            store.add_message(chat_id, message)
        updated = store.get(chat_id)

        # If stream was stopped by user, send a "stopped" event instead of "done"
        if stopped:
            await state.append(json.dumps({"type": "stopped", "chat": updated.model_dump(mode="json")}) + "\n")
        else:
            await state.append(json.dumps({"type": "done", "chat": updated.model_dump(mode="json")}) + "\n")

    await state.finish()
    _active_streams.pop(chat_id, None)


async def handle_send_message(chat_id: str, req: SendMessageRequest) -> AsyncIterator[str]:
    """Start a new generation for ``chat_id`` and stream its events as they
    arrive. If the caller disconnects partway through, generation keeps
    running in the background — see ``start_stream``/``StreamState``."""
    if get_store().get(chat_id) is None:
        raise KeyError(chat_id)
    state = start_stream(chat_id, req)
    async for line in state.subscribe():
        yield line
