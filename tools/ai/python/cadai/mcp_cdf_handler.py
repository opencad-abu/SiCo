"""MCP handlers for CDF inspection, probing, and prepared instance updates."""

from __future__ import annotations

from typing import Any

from .cdf_tools import CDF_NAMES, call_cdf
from .cdf_update import CDF_UPDATE_NAMES, call_cdf_update
from .circuit_spec_schema import CircuitSpecError


class CdfHandlerArgumentError(ValueError):
    """Raised when a CDF request violates its argument contract."""


def dispatch_cdf(
    name: str,
    arguments: dict[str, Any],
    *,
    client: Any,
    workspace: Any,
) -> tuple[bool, dict[str, Any]]:
    """Dispatch one CDF read/probe/update while preserving MCP envelopes."""
    if name in CDF_UPDATE_NAMES:
        if name == "apply_cdf_update":
            raise CdfHandlerArgumentError(
                "Use execute_circuit_operation for CDF writes"
            )
        try:
            return call_cdf_update(name, arguments, client)
        except CircuitSpecError as exc:
            raise CdfHandlerArgumentError(str(exc)) from exc

    if name in CDF_NAMES:
        try:
            return call_cdf(name, arguments, client, workspace)
        except (CircuitSpecError, OSError) as exc:
            raise CdfHandlerArgumentError(str(exc)) from exc

    raise ValueError(f"unknown CDF tool: {name}")


__all__ = ["CdfHandlerArgumentError", "dispatch_cdf"]
