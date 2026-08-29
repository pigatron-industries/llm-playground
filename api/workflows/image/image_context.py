"""Per-turn image generation context.

Stores the user-selected base and model for the active workflow turn so that
image tools can resolve them without the LLM restating them as arguments.
Mirrors the pattern used by ``world_schema.WorldContext``.
"""

from __future__ import annotations

from contextvars import ContextVar


class ImageContext:
    """Holds the selected image base, model, LoRAs, and recent prompt for the active workflow turn."""

    def __init__(self) -> None:
        self._base: ContextVar[str | None] = ContextVar(
            "image_context_base", default=None
        )
        self._model: ContextVar[str | None] = ContextVar(
            "image_context_model", default=None
        )
        self._loras: ContextVar[list[dict] | None] = ContextVar(
            "image_context_loras", default=None
        )
        self._previous_prompt: ContextVar[str | None] = ContextVar(
            "image_context_previous_prompt", default=None
        )

    def set(
        self,
        base: str | None = None,
        model: str | None = None,
        previous_prompt: str | None = None,
    ) -> None:
        if base is not None:
            self._base.set(base)
        if model is not None:
            self._model.set(model)
        if previous_prompt is not None:
            self._previous_prompt.set(previous_prompt)

    def set_loras(self, loras: list[dict] | None = None) -> None:
        """Set the selected LoRAs for the current turn.

        Expects a list of objects like {"name": .., "weight": ..} or None.
        """
        if loras is not None:
            self._loras.set(loras)

    def set_previous_prompt(self, previous_prompt: str | None = None) -> None:
        """Store the most recently used image-generation prompt in the current turn."""
        if previous_prompt is not None:
            self._previous_prompt.set(previous_prompt)

    def get_base(self) -> str | None:
        return self._base.get()

    def get_model(self) -> str | None:
        return self._model.get()

    def get_previous_prompt(self) -> str | None:
        return self._previous_prompt.get()

    def reset(self) -> None:
        self._base.set(None)
        self._model.set(None)
        self._loras.set(None)
        self._previous_prompt.set(None)


image_context = ImageContext()


def set_image_context(
    base: str | None = None,
    model: str | None = None,
    previous_prompt: str | None = None,
) -> None:
    """Set the image generation context for the current turn."""
    image_context.set(base=base, model=model, previous_prompt=previous_prompt)


def set_image_loras(loras: list[dict] | None = None) -> None:
    """Set the LoRAs to apply for the current turn."""
    image_context.set_loras(loras)


def set_image_previous_prompt(previous_prompt: str | None = None) -> None:
    """Store the most recently used image-generation prompt in context."""
    image_context.set_previous_prompt(previous_prompt)


def get_image_base() -> str | None:
    return image_context.get_base()


def get_image_model() -> str | None:
    return image_context.get_model()


def get_image_previous_prompt() -> str | None:
    return image_context.get_previous_prompt()


def get_image_loras() -> list[dict] | None:
    """Return the LoRAs set for the current turn, or None."""
    return image_context._loras.get()
