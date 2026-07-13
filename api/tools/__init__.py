"""Tool definitions and execution for provider-backed chat completions."""

from __future__ import annotations

from . import files as _files  # noqa: F401 - imported for registration side effects
from . import math as _math  # noqa: F401 - imported for registration side effects
from .files import set_project_root
from .registry import execute_registered_tool, get_tool_metadata

__all__ = ["get_tools", "execute_tool", "set_project_root"]


def execute_tool(name: str, arguments: dict) -> str:
    return execute_registered_tool(name, arguments)


def get_tools(*, include_file_tools: bool = False) -> list[dict]:
    """Get available tools, optionally including file system tools."""
    all_tools = get_tool_metadata()
    if include_file_tools:
        return all_tools
    # Filter out file tools for workflows without project context
    return [tool for tool in all_tools if tool["function"]["name"] != "list_files_in_directory"]
