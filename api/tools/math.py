"""Math-related tool definitions."""


def add_numbers(a: float, b: float) -> str:
    return str(a + b)


ADD_NUMBERS_TOOL = {
    "type": "function",
    "function": {
        "name": "add_numbers",
        "description": "Add two numbers together.",
        "parameters": {
            "type": "object",
            "properties": {
                "a": {"type": "number", "description": "First number"},
                "b": {"type": "number", "description": "Second number"},
            },
            "required": ["a", "b"],
            "additionalProperties": False,
        },
    },
}
