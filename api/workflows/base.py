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
    has_state: bool = False  # Whether this workflow renders a state panel in the UI

    def hidden_settings_fields(self) -> set[str]:
        """Names of settings the workflow manages itself, marked ``"hidden"`` in
        its settings schema (e.g. the image workflow's last-generation
        prompt/size, written by its generate tool). The UI form neither renders
        nor edits these, and the server preserves their stored values across
        form updates so a form submission can't clobber them with stale/empty
        defaults."""
        schema = self.settings_model.model_json_schema()
        return {
            name
            for name, field in schema.get("properties", {}).items()
            if field.get("widget") == "hidden"
        }

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

    def get_state(self, chat: Chat) -> dict:
        """Return a serialisable state dict for the UI to render after messages.

        Only called when ``has_state`` is True. The default returns an empty dict;
        override in subclasses to provide workflow-specific state (e.g. a map,
        status panel, or progress indicator).
        """
        return {}
