"""Per-turn image generation context.

Stores the user-selected base and model for the active workflow turn so that
image tools can resolve them without the LLM restating them as arguments.
Mirrors the pattern used by ``world_schema.WorldContext``.
"""

from __future__ import annotations

from contextvars import ContextVar


class ImageContext:
    """Holds the selected image base and model for the active workflow turn."""

    def __init__(self) -> None:
        self._base: ContextVar[str | None] = ContextVar(
            "image_context_base", default=None
        )
        self._model: ContextVar[str | None] = ContextVar(
            "image_context_model", default=None
        )

    def set(self, base: str | None = None, model: str | None = None) -> None:
        if base is not None:
            self._base.set(base)
        if model is not None:
            self._model.set(model)

    def get_base(self) -> str | None:
        return self._base.get()

    def get_model(self) -> str | None:
        return self._model.get()

    def reset(self) -> None:
        self._base.set(None)
        self._model.set(None)


image_context = ImageContext()


def set_image_context(base: str | None = None, model: str | None = None) -> None:
    """Set the image generation context for the current turn."""
    image_context.set(base=base, model=model)


def get_image_base() -> str | None:
    return image_context.get_base()


def get_image_model() -> str | None:
    return image_context.get_model()
