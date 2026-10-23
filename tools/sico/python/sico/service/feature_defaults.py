"""Project-level Codex feature defaults for future sessions."""

from pathlib import Path

from ..codex.feature_groups import (
    describe,
    project_default_path,
    read_project_default,
    write_project_default,
)


def read(project):
    state = Path(project) / ".sico"
    groups = read_project_default(project_default_path(state)) or {}
    return {"groups": describe(groups), "overrides": groups}


def save(project, groups):
    state = Path(project) / ".sico"
    write_project_default(project_default_path(state), groups)
    return read(project)
