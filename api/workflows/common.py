"""Helpers shared by more than one workflow."""

from __future__ import annotations

from ..schemas import Message


def history_for_model(messages: list[Message], preserve_tool_results: bool = False) -> list[Message]:
    """Build provider context, optionally including tool-use entries.

    We keep tool calls/results in persisted chat history for observability.
    By default, we avoid resending them on later turns to reduce prompt bloat.
    Set preserve_tool_results=True to include them for full context awareness.
    """
    sanitized: list[Message] = []
    for message in messages:
        if message.role == "tool":
            if preserve_tool_results:
                sanitized.append(message)
            continue
        if message.role == "assistant" and message.tool_calls:
            if preserve_tool_results:
                # Keep both tool calls and any visible assistant text.
                sanitized.append(message)
            elif message.content:
                # Keep only visible assistant text, strip tool-call metadata.
                sanitized.append(Message(role="assistant", content=message.content))
            continue
        sanitized.append(message)
    return sanitized
