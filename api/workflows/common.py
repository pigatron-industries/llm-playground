"""Helpers shared by more than one workflow."""

from __future__ import annotations

from ..schemas import Message


def history_for_model(messages: list[Message]) -> list[Message]:
    """Build provider context without replaying historical tool-use entries.

    We keep tool calls/results in persisted chat history for observability,
    but avoid resending them on later turns to reduce prompt bloat.
    """
    sanitized: list[Message] = []
    for message in messages:
        if message.role == "tool":
            continue
        if message.role == "assistant" and message.tool_calls:
            if message.content:
                # Keep any visible assistant text, but strip tool-call metadata.
                sanitized.append(Message(role="assistant", content=message.content))
            continue
        sanitized.append(message)
    return sanitized
