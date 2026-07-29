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


class StringReplaceArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str = Field(description="File path relative to project root (e.g., 'api/tools/files.py')")
    search_str: str = Field(description="The string to search for and replace")
    replace_str: str = Field(description="The string to replace with")


@register_tool(StringReplaceArgs, description="Replace all occurrences of a string in a file with another string. Returns an error if the search string is not found.", category="Files")
def string_replace(file: str, search_str: str, replace_str: str) -> str:
    # Get the current project root from context, fallback to hardcoded default
    project_root = _current_project_root.get()
    if project_root is None:
        project_root = Path(__file__).parent.parent.parent

    # Normalize and resolve the path
    target_path = (project_root / file).resolve()

    # Security check: ensure the resolved path is within the project root
    try:
        target_path.relative_to(project_root)
    except ValueError:
        return f"Error: Path '{file}' is outside the project root"

    if not target_path.exists():
        return f"Error: File '{file}' does not exist"

    if not target_path.is_file():
        return f"Error: Path '{file}' is not a file"

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            content = f.read()
    except PermissionError:
        return f"Error: Permission denied reading '{file}'"
    except UnicodeDecodeError:
        return f"Error: File '{file}' is not a valid UTF-8 text file"

    if search_str not in content:
        return f"Error: Search string '{search_str}' not found in '{file}'"

    new_content = content.replace(search_str, replace_str)

    try:
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(new_content)
    except PermissionError:
        return f"Error: Permission denied writing to '{file}'"

    return f"Successfully replaced '{search_str}' with '{replace_str}' in '{file}'"


class AppendFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="File path relative to project root (e.g., 'api/tools/files.py')")
    text: str = Field(description="The text to append to the end of the file")


@register_tool(AppendFileArgs, description="Append text to the end of a file relative to the project root.", category="Edit")
def append_file(path: str, text: str) -> str:
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
        return f"Error: File '{path}' does not exist"

    if not target_path.is_file():
        return f"Error: Path '{path}' is not a file"

    try:
        with open(target_path, "a", encoding="utf-8") as f:
            f.write(text)
        return f"Successfully appended text to '{path}'"
    except PermissionError:
        return f"Error: Permission denied writing to '{path}'"
    except Exception as e:
        return f"Error: Failed to append to '{path}': {e}"


class CreateDirectoryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="Directory path relative to project root (e.g., './' or 'api/tools')")


@register_tool(CreateDirectoryArgs, description="Create a directory within the project. Creates parent directories if they don't exist.", category="Edit")
def create_directory(path: str) -> str:
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

    # Check if the path already exists
    if target_path.exists():
        if target_path.is_dir():
            return f"Directory '{path}' already exists"
        else:
            return f"Error: Path '{path}' already exists and is not a directory"

    # Create the directory
    try:
        target_path.mkdir(parents=True, exist_ok=True)
        return f"Successfully created directory '{path}'"
    except PermissionError:
        return f"Error: Permission denied creating directory '{path}'"
    except Exception as e:
        return f"Error: Failed to create directory '{path}': {e}"
