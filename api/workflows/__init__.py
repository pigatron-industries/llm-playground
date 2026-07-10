"""Workflow definitions and registry.

A workflow bundles the settings a chat needs (as a Pydantic model, rendered
by the UI as a form) with the loop that turns a chat's history plus a new
user message into a stream of chat events. See ``base.py`` for the interface
and ``registry.py`` for how workflows are looked up. Importing this package
registers every built-in workflow as a side effect.
"""

from __future__ import annotations

from . import simple_chat as _simple_chat  # noqa: F401 - registration side effect
from .base import Workflow, WorkflowContext
from .registry import DEFAULT_WORKFLOW_ID, get_workflow, list_workflows

__all__ = [
    "Workflow",
    "WorkflowContext",
    "DEFAULT_WORKFLOW_ID",
    "get_workflow",
    "list_workflows",
]
