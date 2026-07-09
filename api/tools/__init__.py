"""Tool definitions and execution for provider-backed chat completions."""

from __future__ import annotations

import json

from .math import ADD_NUMBERS_TOOL, add_numbers

DEFAULT_TOOLS = [ADD_NUMBERS_TOOL]

__all__ = ["ADD_NUMBERS_TOOL", "DEFAULT_TOOLS", "execute_tool"]


def execute_tool(name: str, arguments: dict) -> str:
    if name == "add_numbers":
        return add_numbers(arguments["a"], arguments["b"])
    return json.dumps({"error": f"Unknown tool: {name}"})
