"""Per-tool-call "vision review" hand-off.

A tool that produces images (e.g. ``generate_image``) can stash their data
URLs here; the provider loop (``api.providers.chat_stream``) then appends an
extra user message containing the images right after the tool result, so a
vision-capable model can actually see what it generated before responding.

The OpenAI-compatible API only allows plain-string content on ``tool`` role
messages, which is why the images travel in a separate user message instead
of inside the tool result itself.
"""

from __future__ import annotations

from contextvars import ContextVar

_review_images: ContextVar[list[str]] = ContextVar("vision_review_images", default=[])


def set_review_images(data_urls: list[str]) -> None:
    """Stash image data URLs produced by the tool that is about to return."""
    _review_images.set(list(data_urls))


def pop_review_images() -> list[str]:
    """Read and clear the stashed data URLs (the provider loop calls this
    once per tool execution)."""
    urls = list(_review_images.get())
    _review_images.set([])
    return urls
