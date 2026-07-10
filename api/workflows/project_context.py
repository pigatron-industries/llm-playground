"""Reads every file in a selected project and injects it as chat context."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ..project_store import get_project_store
from ..providers import ChatEvent, get_client
from ..schemas import Chat, Message
from ..tools import DEFAULT_TOOLS
from .base import Workflow, WorkflowContext
from .common import history_for_model
from .registry import register_workflow

# Soft cap on how much project text gets concatenated into context, so a
# large project degrades to a truncation notice instead of an oversized
# request the provider will reject or take forever on.
_MAX_CONTEXT_CHARS = 200_000


class ProjectContextSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(json_schema_extra={"widget": "model_select"})
    project_id: str = Field(
        description="Project whose files are read into context on every turn",
        json_schema_extra={"widget": "project_select"},
    )
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)


def _read_project_files(root: Path) -> str:
    """Concatenate every readable text file under ``root``.

    Skips hidden files/dirs (``.git``, ``.venv``, ...) and anything that
    isn't valid UTF-8 text; stops once the total exceeds the size cap.
    """
    parts: list[str] = []
    total = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        header = f"--- {path.relative_to(root)} ---\n"
        parts.append(header + content)
        total += len(header) + len(content)
        if total >= _MAX_CONTEXT_CHARS:
            parts.append("[project context truncated — too large to include in full]")
            break
    return "\n\n".join(parts)


@register_workflow
class ProjectContextWorkflow(Workflow):
    id = "project_context"
    name = "Project context"
    description = "Reads every file in the selected project and adds it to the chat's context."
    settings_model = ProjectContextSettings

    async def run(self, ctx: WorkflowContext) -> AsyncIterator[ChatEvent]:
        settings = ProjectContextSettings.model_validate(ctx.chat.workflow_settings)
        project = get_project_store().get(settings.project_id)
        if project is None:
            raise ValueError(f"Project '{settings.project_id}' no longer exists")

        file_context = _read_project_files(Path(project.path))

        conversation: list[Message] = [
            Message(
                role="system",
                content=(
                    f"The user is working in the project '{project.name}'. Here are the "
                    f"contents of every file in the project:\n\n{file_context}"
                ),
            )
        ]
        conversation.extend(history_for_model(ctx.chat.messages))
        conversation.append(ctx.user_message)

        client = get_client()
        async for event in client.chat_stream(
            model=settings.model,
            messages=conversation,
            temperature=settings.temperature,
            tools=DEFAULT_TOOLS,
        ):
            yield event

    def extra_context_chars(self, chat: Chat) -> int:
        settings = ProjectContextSettings.model_validate(chat.workflow_settings)
        project = get_project_store().get(settings.project_id)
        if project is None:
            return 0
        return len(_read_project_files(Path(project.path)))
