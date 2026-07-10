"""Math-related tool definitions."""

from pydantic import BaseModel, ConfigDict, Field

from .registry import register_tool


class AddNumbersArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    a: float = Field(description="First number")
    b: float = Field(description="Second number")


@register_tool(AddNumbersArgs, description="Add two numbers together.")
def add_numbers(a: float, b: float) -> str:
    return str(a + b)
