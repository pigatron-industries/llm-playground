"""Shared registry for local tools exposed to the LLM."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, ValidationError


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    description: str
    params_model: type[BaseModel]
    handler: Callable[..., str]
    category: str = "General"

    def metadata(self) -> dict[str, Any]:
        """Render OpenAI-compatible tool metadata from the Pydantic model."""
        schema = self.params_model.model_json_schema()
        schema.pop("title", None)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
                "category": self.category,
            },
        }


_TOOLS: dict[str, RegisteredTool] = {}


def register_tool(
    params_model: type[BaseModel],
    *,
    name: str | None = None,
    description: str | None = None,
    category: str = "General",
) -> Callable[[Callable[..., str]], Callable[..., str]]:
    """Decorator to register a callable as an LLM tool."""

    def decorator(func: Callable[..., str]) -> Callable[..., str]:
        tool_name = name or func.__name__
        tool_description = (description or func.__doc__ or "").strip() or f"Tool: {tool_name}"
        _TOOLS[tool_name] = RegisteredTool(
            name=tool_name,
            description=tool_description,
            params_model=params_model,
            handler=func,
            category=category,
        )
        return func

    return decorator


def get_tool_metadata() -> list[dict[str, Any]]:
    return [tool.metadata() for tool in _TOOLS.values()]


def execute_registered_tool(name: str, arguments: dict[str, Any]) -> str:
    tool = _TOOLS.get(name)
    if tool is None:
        return json.dumps({"error": f"Unknown tool: {name}"})

    try:
        validated = tool.params_model.model_validate(arguments)
    except ValidationError as exc:
        return json.dumps(
            {
                "error": f"Invalid arguments for tool '{name}'",
                "details": exc.errors(),
            }
        )

    return tool.handler(**validated.model_dump())