"""MCP handler for circuit target inspection and creation lifecycle."""

from __future__ import annotations

from typing import Any

from .circuit_create import CREATE_NAMES, call_create
from .circuit_spec_schema import CircuitSpecError
from .pdk_schema import PdkUnavailable
from .template_catalog import TemplateUnavailable


class CreationHandlerArgumentError(ValueError):
    """Raised when circuit creation arguments are invalid."""


def dispatch_creation(
    name: str,
    arguments: dict[str, Any],
    *,
    client: Any,
    workspace: Any,
    pdk_bindings: Any = None,
) -> tuple[bool, dict[str, Any]]:
    """Dispatch circuit creation while preserving unavailable-domain envelopes."""
    if name not in CREATE_NAMES:
        raise ValueError(f"unknown creation tool: {name}")
    try:
        return call_create(name, arguments, client, workspace=workspace, pdk_bindings=pdk_bindings)
    except (CircuitSpecError, OSError) as exc:
        raise CreationHandlerArgumentError(str(exc)) from exc
    except TemplateUnavailable as exc:
        return False, {"code": "template_circuit_unavailable", "message": str(exc)}
    except PdkUnavailable as exc:
        return False, {"code": exc.code, "message": str(exc)}


__all__ = ["CREATE_NAMES", "CreationHandlerArgumentError", "dispatch_creation"]
