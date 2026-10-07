"""MCP handler for project preflight and durable circuit operations."""

from __future__ import annotations

from typing import Any

from .circuit_spec_schema import CircuitSpecError
from .project_schema import PROJECT_NAMES
from .project_tools import call_project


class ProjectHandlerArgumentError(ValueError):
    """Raised when a project tool receives invalid arguments."""


def dispatch_project(
    name: str,
    arguments: dict[str, Any],
    *,
    client: Any,
    workspace: Any,
    definitions: Any,
    pdk_bindings: Any = None,
) -> dict[str, Any]:
    """Dispatch project tooling while leaving journal state with project_tools."""
    if name not in PROJECT_NAMES:
        raise ValueError(f"unknown project tool: {name}")
    try:
        return call_project(name, arguments, client, workspace, definitions, pdk_bindings)
    except (CircuitSpecError, OSError) as exc:
        raise ProjectHandlerArgumentError(str(exc)) from exc


__all__ = ["PROJECT_NAMES", "ProjectHandlerArgumentError", "dispatch_project"]
