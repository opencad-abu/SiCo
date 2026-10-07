"""MCP handler for reviewed in-place circuit edits."""

from __future__ import annotations

from typing import Any

from .circuit_edit import EDIT_NAMES, call_edit
from .circuit_spec_schema import CircuitSpecError
from .pdk_schema import PdkUnavailable


class EditHandlerArgumentError(ValueError):
    """Raised when circuit edit arguments are invalid."""


def dispatch_edit(
    name: str,
    arguments: dict[str, Any],
    *,
    client: Any,
    workspace: Any,
    pdk_bindings: Any = None,
) -> tuple[bool, dict[str, Any]]:
    """Dispatch reviewed circuit edits without owning approval state."""
    if name not in EDIT_NAMES:
        raise ValueError(f"unknown edit tool: {name}")
    try:
        return call_edit(name, arguments, client, workspace=workspace, pdk_bindings=pdk_bindings)
    except (CircuitSpecError, OSError) as exc:
        raise EditHandlerArgumentError(str(exc)) from exc
    except PdkUnavailable as exc:
        return False, {"code": exc.code, "message": str(exc)}


__all__ = ["EDIT_NAMES", "EditHandlerArgumentError", "dispatch_edit"]
