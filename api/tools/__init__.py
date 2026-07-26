"""Tool definitions and execution for provider-backed chat completions."""

from __future__ import annotations

from . import edit as _edit  # noqa: F401 - imported for registration side effects
from . import files as _files  # noqa: F401 - imported for registration side effects
from . import math as _math  # noqa: F401 - imported for registration side effects
from .files import set_project_root
from .registry import execute_registered_tool, get_tool_metadata

__all__ = ["get_tools", "execute_tool", "set_project_root"]


def execute_tool(name: str, arguments: dict) -> str:
    return execute_registered_tool(name, arguments)


def get_tools(tool_types: list[str] | None = None) -> list[dict]:
    """Get available tools, filtered by category (e.g., 'Math', 'Files', 'Edit')."""
    all_tools = get_tool_metadata()

    if tool_types is None:
        return all_tools

    return [tool for tool in all_tools if tool["function"]["category"] in tool_types]