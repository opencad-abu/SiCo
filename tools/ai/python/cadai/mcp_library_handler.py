"""MCP handler for project library preparation and creation."""

from __future__ import annotations

from typing import Any

from .circuit_library import LIBRARY_NAMES, call_library
from .circuit_spec_schema import CircuitSpecError


class LibraryHandlerArgumentError(ValueError):
    """Raised when a library tool receives invalid arguments."""


def dispatch_library(
    name: str,
    arguments: dict[str, Any],
    *,
    client: Any,
    workspace: Any,
) -> tuple[bool, dict[str, Any]]:
    """Dispatch library preparation while preserving its result envelope."""
    if name not in LIBRARY_NAMES:
        raise ValueError(f"unknown library tool: {name}")
    if name == "create_circuit_library":
        raise LibraryHandlerArgumentError(
            "Use execute_circuit_operation for library creation"
        )
    try:
        return call_library(name, arguments, client, workspace)
    except (CircuitSpecError, OSError) as exc:
        raise LibraryHandlerArgumentError(str(exc)) from exc


__all__ = ["LIBRARY_NAMES", "LibraryHandlerArgumentError", "dispatch_library"]
