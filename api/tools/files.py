"""File system-related tool definitions."""

import contextvars
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .registry import register_tool

# Context variable to store the current project root
_current_project_root: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "current_project_root", default=None
)


def set_project_root(root: Path | str) -> None:
    """Set the project root for tools to use."""
    _current_project_root.set(Path(root))


class ListFilesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="Directory path relative to project root (e.g., './' or 'api/tools')")
    recursive: bool = Field(default=False, description="Recursively list all subdirectories")
    include_hidden: bool = Field(default=False, description="Include hidden files and directories (starting with .)")


@register_tool(ListFilesArgs, description="List files and directories in a given directory relative to the project root.")
def list_files_in_directory(path: str, recursive: bool = False, include_hidden: bool = False) -> str:
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

    if not target_path.exists():
        return f"Error: Path '{path}' does not exist"

    if not target_path.is_dir():
        return f"Error: Path '{path}' is not a directory"

    def should_skip(item_path: Path) -> bool:
        """Check if item should be skipped (hidden files)."""
        if include_hidden:
            return False
        return item_path.name.startswith(".")

    try:
        if not recursive:
            items = sorted(target_path.iterdir())
            lines = [f"Contents of {path}:"]
            for item in items:
                if should_skip(item):
                    continue
                if item.is_dir():
                    lines.append(f"  [DIR]  {item.name}/")
                else:
                    size = item.stat().st_size
                    lines.append(f"  [FILE] {item.name} ({size} bytes)")
        else:
            lines = [f"Recursive contents of {path}:"]
            for root, dirs, files in os.walk(target_path):
                level = len(Path(root).relative_to(target_path).parts)
                indent = "  " * level
                rel_root = Path(root).relative_to(target_path)
                if level > 0:
                    lines.append(f"{indent}[DIR] {rel_root.name}/")

                for file in sorted(files):
                    file_path = Path(root) / file
                    if should_skip(file_path):
                        continue
                    size = file_path.stat().st_size
                    lines.append(f"{indent}  [FILE] {file} ({size} bytes)")

                # Filter out hidden dirs for the next iteration
                if not include_hidden:
                    dirs[:] = [d for d in dirs if not d.startswith(".")]

                for dir_name in sorted(dirs):
                    if level == 0:
                        lines.append(f"{indent}  [DIR]  {dir_name}/")
    except PermissionError:
        return f"Error: Permission denied accessing '{path}'"

    return "\n".join(lines)


class ReadFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="File path relative to project root (e.g., 'api/tools/files.py')")


@register_tool(ReadFileArgs, description="Read the contents of a file relative to the project root.")
def read_file(path: str) -> str:
    # Get the current project root from context, fallback to hardcoded default
    project_root = _current_project_root.get()
    if project_root is None:
        project_root = Path(__file__).parent.parent.parent

    # Normalize and resolve the path
    target_path = (project_root / path).resolve()

    print(f"Reading file: {target_path}")  # Debugging statement

    # Security check: ensure the resolved path is within the project root
    try:
        target_path.relative_to(project_root)
    except ValueError:
        return f"Error: Path '{path}' is outside the project root"

    if not target_path.exists():
        return f"Error: File '{path}' does not exist"

    if not target_path.is_file():
        return f"Error: Path '{path}' is not a file"

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            return f.read()
    except PermissionError:
        return f"Error: Permission denied reading '{path}'"
    except UnicodeDecodeError:
        return f"Error: File '{path}' is not a valid UTF-8 text file"
