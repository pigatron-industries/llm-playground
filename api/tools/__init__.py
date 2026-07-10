"""Tool definitions and execution for provider-backed chat completions."""

from __future__ import annotations

from . import math as _math  # noqa: F401 - imported for registration side effects
from .registry import execute_registered_tool, get_tool_metadata

DEFAULT_TOOLS = get_tool_metadata()

__all__ = ["DEFAULT_TOOLS", "execute_tool"]


def execute_tool(name: str, arguments: dict) -> str:
    return execute_registered_tool(name, arguments)
