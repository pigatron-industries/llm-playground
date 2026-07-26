"""The default workflow: a plain back-and-forth chat with tools."""

from __future__ import annotations

from collections.abc import AsyncIterator

from pydantic import BaseModel, ConfigDict, Field

from ..providers import ChatEvent, get_client
from ..schemas import Chat, Message
from ..tools import get_tools, set_project_root
from ..project_store import get_project_store
from .base import Workflow, WorkflowContext
from .common import history_for_model
from .registry import register_workflow


TOOL_TYPES = ["Math", "Files", "Edit"]


class ProjectToolsSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(
        description="Project whose files are read into context on every turn",
        json_schema_extra={"widget": "project_select"},
    )
    model: str = Field(json_schema_extra={"widget": "model_select"})
    system_prompt: str = Field(
        default="",
        description="Optional instructions for the assistant",
        json_schema_extra={"widget": "textarea"},
    )
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    preserve_tool_results: bool = Field(
        default=False,
        description="Keep tool call results in the conversation sent to the model for full context",
    )


@register_workflow
class ProjectToolsWorkflow(Workflow):
    id = "project_tools"
    name = "Project tools"
    description = "Chat with tools to allow reading and writing files in a project."
    settings_model = ProjectToolsSettings

    async def run(self, ctx: WorkflowContext) -> AsyncIterator[ChatEvent]:
        settings = ProjectToolsSettings.model_validate(ctx.chat.workflow_settings)
        project = get_project_store().get(settings.project_id)
        if project is None:
            raise ValueError(f"Project '{settings.project_id}' no longer exists")

        set_project_root(project.path)

        conversation: list[Message] = []
        if settings.system_prompt:
            conversation.append(Message(role="system", content=settings.system_prompt))
        conversation.extend(history_for_model(ctx.chat.messages, preserve_tool_results=settings.preserve_tool_results))
        conversation.append(ctx.user_message)

        client = get_client()
        async for event in client.chat_stream(
            model=settings.model,
            messages=conversation,
            temperature=settings.temperature,
            tools=get_tools(tool_types=TOOL_TYPES),
        ):
            yield event

    def extra_context_chars(self, chat: Chat) -> int:
        settings = SimpleChatSettings.model_validate(chat.workflow_settings)
        return len(settings.system_prompt)
