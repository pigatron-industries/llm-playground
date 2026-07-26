"""File editing tool definitions."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .files import _current_project_root
from .registry import register_tool


class WriteFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="File path relative to project root (e.g., 'api/tools/files.py')")
    contents: str = Field(description="The full contents of the file to create or replace")


@register_tool(WriteFileArgs, description="Create or replace a file with the given contents relative to the project root.", category="Edit")
def write_file(path: str, contents: str) -> str:
    # Get the current project root from context, fallback to hardcoded default
    project_root = _current_project_root.get()
    if project_root is None:
        project_root = Path(__file__).parent.parent.parent

    # Normalize and resolve the path
    target_path = (project_root / path).resolve()

    # Security check: ensure the resolved path is within the project root
    try:
        target_path.relative_to(project_root)
    except ValueError:
        return f"Error: Path '{path}' is outside the project root"

    # Ensure parent directory exists
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        return f"Error: Permission denied creating directory for '{path}'"

    # Write the file
    try:
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(contents)
        return f"Successfully wrote to '{path}'"
    except PermissionError:
        return f"Error: Permission denied writing to '{path}'"
    except Exception as e:
        return f"Error: Failed to write to '{path}': {e}"