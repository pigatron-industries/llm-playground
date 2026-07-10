"""Workflow abstraction: pluggable chat-loop strategies.

A workflow owns two things:

* a Pydantic ``settings_model`` describing what it needs configured (the UI
  renders a form from its JSON Schema rather than hard-coding inputs per
  workflow — see ``registry.list_workflows``);
* a ``run`` method that turns a chat's persisted history plus a new user
  message into a stream of the same ``ChatEvent``s a single model call
  produces, so the wire protocol and persistence in
  ``api.service.chat.handle_send_message`` don't need to know which workflow
  is running.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass

from pydantic import BaseModel

from ..providers import ChatEvent
from ..schemas import Chat, Message


@dataclass(frozen=True)
class WorkflowContext:
    """Everything a workflow needs for one turn.

    ``chat.workflow_settings`` holds this workflow's settings (validate
    against ``settings_model`` before reading); ``chat.messages`` is the
    persisted history so far, not yet including ``user_message``.
    """

    chat: Chat
    user_message: Message


class Workflow(ABC):
    id: str
    name: str
    description: str
    settings_model: type[BaseModel]

    @abstractmethod
    def run(self, ctx: WorkflowContext) -> AsyncIterator[ChatEvent]:
        """Yield chat events for this turn; the final event must be a StreamComplete."""
        raise NotImplementedError

    def extra_context_chars(self, chat: Chat) -> int:
        """Best-effort character count of context this workflow injects beyond
        the visible chat history — a system prompt, injected file contents,
        etc. Used only for the UI's context-window usage estimate, so an
        approximation (or 0, the default for workflows with nothing extra)
        is fine; never raise here."""
        return 0
