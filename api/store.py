"""Chat storage.

An in-memory store keyed by chat id. It's deliberately behind a small interface
(:class:`ChatStore`) so it can be swapped for a database- or file-backed
implementation later without touching the routes.

Note: in-memory means chats are lost when the server restarts (including on
auto-reload). That's fine for a local test harness; persist to disk/DB when you
need durability.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from .schemas import Chat, ChatSummary, Message


def _now() -> datetime:
    return datetime.now(timezone.utc)


_DEFAULT_TITLE = "New chat"


class ChatStore:
    def __init__(self) -> None:
        self._chats: dict[str, Chat] = {}

    def create(self, title: str | None = None, model: str | None = None) -> Chat:
        chat_id = uuid.uuid4().hex
        now = _now()
        chat = Chat(
            id=chat_id,
            title=title or _DEFAULT_TITLE,
            model=model,
            created_at=now,
            updated_at=now,
            messages=[],
        )
        self._chats[chat_id] = chat
        return chat

    def get(self, chat_id: str) -> Chat | None:
        return self._chats.get(chat_id)

    def list(self) -> list[ChatSummary]:
        ordered = sorted(
            self._chats.values(), key=lambda c: c.updated_at, reverse=True
        )
        return [self._summarise(c) for c in ordered]

    def add_message(self, chat_id: str, message: Message) -> Chat:
        chat = self._chats[chat_id]
        chat.messages.append(message)
        chat.updated_at = _now()
        # Give the chat a sensible title from the first user message.
        if chat.title == _DEFAULT_TITLE and message.role == "user":
            first_line = message.content.strip().splitlines()
            if first_line:
                chat.title = first_line[0][:50]
        return chat

    def set_model(self, chat_id: str, model: str) -> None:
        self._chats[chat_id].model = model

    def delete(self, chat_id: str) -> bool:
        return self._chats.pop(chat_id, None) is not None

    @staticmethod
    def _summarise(chat: Chat) -> ChatSummary:
        return ChatSummary(
            id=chat.id,
            title=chat.title,
            model=chat.model,
            created_at=chat.created_at,
            updated_at=chat.updated_at,
            message_count=len(chat.messages),
        )


_store = ChatStore()


def get_store() -> ChatStore:
    return _store
