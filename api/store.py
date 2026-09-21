"""Chat storage.

Chats are persisted as one JSON file per chat in a configurable directory
(see :func:`api.config.get_chats_dir`). The chat id — and hence the filename —
is ``chat-YYYYMMDD-HHMMSS-N``, where the date/time is the chat's start moment
and ``N`` disambiguates chats created in the same second, giving files like
``chat-20260828-121421-1.json``. A chat's generated images live in a folder
of the same name alongside that file (``chat-20260828-121421-1/``). The store
is behind a small interface so it can be swapped for a DB implementation
later without touching the routes.
"""

from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .config import get_chats_dir
from .schemas import Chat, ChatSummary, Message

log = logging.getLogger("llm_harness.store")


def _now() -> datetime:
    return datetime.now(timezone.utc)


_DEFAULT_TITLE = "New chat"


class ChatStore:
    """File-backed chat store."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    # --- paths / io ---------------------------------------------------

    def _path(self, chat_id: str) -> Path:
        return self.directory / f"{chat_id}.json"

    def _write(self, chat: Chat) -> None:
        # Write to a temp file then atomically replace, so a crash mid-write
        # can't leave a half-written (corrupt) chat file.
        path = self._path(chat.id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(chat.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def _read(self, path: Path) -> Chat | None:
        try:
            return Chat.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — skip unreadable/corrupt files
            log.warning("Skipping unreadable chat file: %s", path)
            return None

    # --- interface ----------------------------------------------------

    def _new_id(self, created_at: datetime) -> str:
        """Build a human-readable chat id: ``chat-YYYYMMDD-HHMMSS-N``.

        The date/time is the chat's start moment (the value used for
        ``created_at``); ``N`` is the smallest positive integer not already
        taken by a chat created in the same second (scans the stored files).
        """
        stem = f"chat-{created_at:%Y%m%d-%H%M%S}"
        next_n = 1
        for path in self.directory.glob(f"{stem}-*.json"):
            suffix = path.stem.rsplit("-", 1)[-1]
            if suffix.isdigit():
                next_n = max(next_n, int(suffix) + 1)
        return f"{stem}-{next_n}"

    def create(
        self,
        title: str | None = None,
        workflow_id: str = "simple_chat",
        workflow_settings: dict | None = None,
    ) -> Chat:
        now = _now()
        chat = Chat(
            id=self._new_id(now),
            title=title or _DEFAULT_TITLE,
            workflow_id=workflow_id,
            workflow_settings=workflow_settings or {},
            created_at=now,
            updated_at=now,
            messages=[],
        )
        self._write(chat)
        return chat


    def get(self, chat_id: str) -> Chat | None:
        path = self._path(chat_id)
        return self._read(path) if path.is_file() else None


    def list(self, project_id: str | None = None) -> list[ChatSummary]:
        chats = [c for c in (self._read(p) for p in self.directory.glob("*.json")) if c]

        # Filter by project_id if provided
        if project_id is not None:
            # Only show chats linked to this specific project
            chats = [
                c for c in chats
                if c.workflow_settings.get("project_id") == project_id
            ]
        else:
            # No project selected: only show chats with no project link
            chats = [
                c for c in chats
                if not c.workflow_settings.get("project_id")
            ]

        chats.sort(key=lambda c: c.updated_at, reverse=True)
        return [self._summarise(c) for c in chats]


    def add_message(self, chat_id: str, message: Message) -> Chat:
        chat = self.get(chat_id)
        if chat is None:
            raise KeyError(chat_id)
        chat.messages.append(message)
        chat.updated_at = _now()
        # Give the chat a sensible title from the first user message.
        if chat.title == _DEFAULT_TITLE and message.role == "user":
            first_line = message.content.strip().splitlines()
            if first_line:
                chat.title = first_line[0][:50]
        self._write(chat)
        return chat

    def update(
        self,
        chat_id: str,
        title: str | None = None,
        workflow_id: str | None = None,
        workflow_settings: dict | None = None,
    ) -> Chat:
        chat = self.get(chat_id)
        if chat is None:
            raise KeyError(chat_id)
        if title is not None:
            chat.title = title
        if workflow_id is not None:
            chat.workflow_id = workflow_id
        if workflow_settings is not None:
            chat.workflow_settings = workflow_settings
        chat.updated_at = _now()
        self._write(chat)
        return chat

    def delete(self, chat_id: str) -> bool:
        path = self._path(chat_id)
        deleted = False
        if path.is_file():
            path.unlink()
            deleted = True
        # Remove the chat's images folder (same name as the chat file,
        # alongside it) so deleting a chat doesn't leave orphaned images.
        images = self.directory / chat_id
        if images.is_dir():
            shutil.rmtree(images, ignore_errors=True)
            deleted = True
        return deleted

    def remove_message(self, chat_id: str, index: int) -> Chat:
        """Remove a message at the given index from a chat's history."""
        chat = self.get(chat_id)
        if chat is None:
            raise KeyError(chat_id)
        if not 0 <= index < len(chat.messages):
            raise ValueError(f"Message index {index} out of range")
        chat.messages.pop(index)
        chat.updated_at = _now()
        self._write(chat)
        return chat

    @staticmethod
    def _summarise(chat: Chat) -> ChatSummary:
        return ChatSummary(
            id=chat.id,
            title=chat.title,
            workflow_id=chat.workflow_id,
            created_at=chat.created_at,
            updated_at=chat.updated_at,
            message_count=len(chat.messages),
        )


_store: ChatStore | None = None


def get_store() -> ChatStore:
    # Created lazily so CHATS_DIR set at startup is honoured.
    global _store
    if _store is None:
        _store = ChatStore(get_chats_dir())
    return _store
