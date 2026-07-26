"""Project storage backed by a JSON file in the data directory."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from .config import get_projects_file
from .schemas import Project


class ProjectStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> list[Project]:
        if not self.path.exists():
            return []
        try:
            data = self.path.read_text(encoding="utf-8")
            if not data.strip():
                return []
            payload = Project.model_validate_json(data) if False else None
        except Exception:
            payload = None
        if payload is not None:
            return payload
        try:
            raw = self.path.read_text(encoding="utf-8")
            items = []
            for item in __import__("json").loads(raw):
                items.append(Project.model_validate(item))
            return items
        except Exception:
            return []

    def _write(self, projects: list[Project]) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(__import__("json").dumps([p.model_dump() for p in projects], indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def list(self) -> list[Project]:
        return self._read()

    def get(self, project_id: str) -> Project | None:
        return next((p for p in self._read() if p.id == project_id), None)

    def create(self, name: str, path: str) -> Project:
        project = Project(id=uuid.uuid4().hex, name=name, path=path)
        projects = self._read()
        projects.append(project)
        self._write(projects)
        return project

    def delete(self, project_id: str) -> bool:
        projects = self._read()
        remaining = [project for project in projects if project.id != project_id]
        if len(remaining) == len(projects):
            return False
        self._write(remaining)
        return True

    def update(self, project_id: str, name: str | None = None, path: str | None = None) -> Project | None:
        projects = self._read()
        for i, project in enumerate(projects):
            if project.id == project_id:
                if name is not None:
                    project.name = name
                if path is not None:
                    project.path = path
                projects[i] = project
                self._write(projects)
                return project
        return None


_store: ProjectStore | None = None


def get_project_store() -> ProjectStore:
    global _store
    if _store is None:
        _store = ProjectStore(get_projects_file())
    return _store
