"""Registry of available workflows.

Mirrors the pattern already used for tools (``api/tools/registry.py``):
a decorator registers an instance, and metadata is rendered from a Pydantic
model's JSON Schema rather than hand-written.
"""

from __future__ import annotations

from ..schemas import WorkflowInfo
from .base import Workflow

DEFAULT_WORKFLOW_ID = "simple_chat"

_WORKFLOWS: dict[str, Workflow] = {}


def register_workflow(cls: type[Workflow]) -> type[Workflow]:
    """Class decorator that instantiates and registers a workflow."""
    instance = cls()
    _WORKFLOWS[instance.id] = instance
    return cls


def get_workflow(workflow_id: str) -> Workflow:
    workflow = _WORKFLOWS.get(workflow_id)
    if workflow is None:
        known = ", ".join(sorted(_WORKFLOWS))
        raise KeyError(f"Unknown workflow '{workflow_id}'. Known workflows: {known}")
    return workflow


def list_workflows() -> list[WorkflowInfo]:
    return [
        WorkflowInfo(
            id=workflow.id,
            name=workflow.name,
            description=workflow.description,
            settings_schema=_schema_for(workflow),
            has_state=workflow.has_state,
        )
        for workflow in _WORKFLOWS.values()
    ]


def _schema_for(workflow: Workflow) -> dict:
    schema = workflow.settings_model.model_json_schema()
    schema.pop("title", None)
    return schema
