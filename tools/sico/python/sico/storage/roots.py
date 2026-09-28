"""Authoritative project state-root selection and process environment."""

from __future__ import annotations

from sicostate import export_environment, project_directory, project_root


def state_root(project, *, create=False):
    return project_root(project, create=create)


def agent_root(project, *, create=False):
    return project_directory(project, "ai/agent", create=create)


def export_state_environment(state, environment):
    """Export the one temporary-root contract for a selected project state."""
    state = state_root(state.parent if state.name in {".sico", ".cad"} else state)
    return export_environment(environment, state)
