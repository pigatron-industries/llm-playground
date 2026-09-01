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
        self._prompt: ContextVar[str | None] = ContextVar(
            "image_context_prompt", default=None
        )
        self._negprompt: ContextVar[str | None] = ContextVar(
            "image_context_negprompt", default=None
        )
        self._width: ContextVar[int | None] = ContextVar(
            "image_context_width", default=None
        )
        self._height: ContextVar[int | None] = ContextVar(
            "image_context_height", default=None
        )
        self._chat_id: ContextVar[str | None] = ContextVar(
            "image_context_chat_id", default=None
        )

    def set(
        self,
        base: str | None = None,
        model: str | None = None,
        prompt: str | None = None,
        negprompt: str | None = None,
        width: int | None = None,
        height: int | None = None,
        chat_id: str | None = None,
    ) -> None:
        if base is not None:
            self._base.set(base)
        if model is not None:
            self._model.set(model)
        if prompt is not None:
            self._prompt.set(prompt)
        if negprompt is not None:
            self._negprompt.set(negprompt)
        if width is not None:
            self._width.set(width)
        if height is not None:
            self._height.set(height)
        if chat_id is not None:
            self._chat_id.set(chat_id)

    def set_loras(self, loras: list[dict] | None = None) -> None:
        """Set the selected LoRAs for the current turn.

        Expects a list of objects like {"name": .., "weight": ..} or None.
        """
        if loras is not None:
            self._loras.set(loras)

    def set_prompt(self, prompt: str | None = None) -> None:
        """Store the image-generation prompt for the current turn."""
        if prompt is not None:
            self._prompt.set(prompt)

    def get_base(self) -> str | None:
        return self._base.get()

    def get_model(self) -> str | None:
        return self._model.get()

    def get_prompt(self) -> str | None:
        return self._prompt.get()

    def get_negprompt(self) -> str | None:
        return self._negprompt.get()

    def get_width(self) -> int | None:
        return self._width.get()

    def get_height(self) -> int | None:
        return self._height.get()

    def get_chat_id(self) -> str | None:
        return self._chat_id.get()

    def reset(self) -> None:
        self._base.set(None)
        self._model.set(None)
        self._loras.set(None)
        self._prompt.set(None)
        self._negprompt.set(None)
        self._width.set(None)
        self._height.set(None)
        self._chat_id.set(None)


image_context = ImageContext()


def set_image_context(
    base: str | None = None,
    model: str | None = None,
    prompt: str | None = None,
    negprompt: str | None = None,
    width: int | None = None,
    height: int | None = None,
    chat_id: str | None = None,
) -> None:
    """Set the image generation context for the current turn."""
    image_context.set(base=base, model=model, prompt=prompt, negprompt=negprompt, width=width, height=height, chat_id=chat_id)


def set_image_loras(loras: list[dict] | None = None) -> None:
    """Set the LoRAs to apply for the current turn."""
    image_context.set_loras(loras)


def set_image_prompt(prompt: str | None = None) -> None:
    """Store the image-generation prompt for the current turn."""
    image_context.set_prompt(prompt)


def set_image_negprompt(negprompt: str | None = None) -> None:
    """Store the image-generation negative prompt for the current turn."""
    image_context.set(negprompt=negprompt)


def set_image_width(width: int | None = None) -> None:
    """Store the image-generation width for the current turn."""
    image_context.set(width=width)


def set_image_height(height: int | None = None) -> None:
    """Store the image-generation height for the current turn."""
    image_context.set(height=height)


def get_image_base() -> str | None:
    return image_context.get_base()


def get_image_chat_id() -> str | None:
    """The id of the chat the current turn belongs to, or None outside a chat."""
    return image_context.get_chat_id()


def get_image_model() -> str | None:
    return image_context.get_model()


def get_image_prompt() -> str | None:
    return image_context.get_prompt()


def get_image_negprompt() -> str | None:
    return image_context.get_negprompt()


def get_image_width() -> int | None:
    return image_context.get_width()


def get_image_height() -> int | None:
    return image_context.get_height()


def get_image_loras() -> list[dict] | None:
    """Return the LoRAs set for the current turn, or None."""
    return image_context._loras.get()
