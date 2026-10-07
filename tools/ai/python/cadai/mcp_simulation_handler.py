"""MCP handler for the explicit simulation setup lifecycle."""

from __future__ import annotations

from typing import Any

from .circuit_spec_schema import CircuitSpecError
from .simulation_recipe import SIMULATION_NAMES, call_simulation


class SimulationHandlerArgumentError(ValueError):
    """Raised when a simulation tool has invalid arguments."""


def dispatch_simulation(
    name: str,
    arguments: dict[str, Any],
    *,
    client: Any,
) -> tuple[bool, dict[str, Any]]:
    """Dispatch one simulation lifecycle operation without owning server state."""
    if name not in SIMULATION_NAMES:
        raise ValueError(f"unknown simulation tool: {name}")
    try:
        return call_simulation(name, arguments, client)
    except (CircuitSpecError, OSError) as exc:
        # Keep malformed recipe/setup requests on the MCP invalid-params path.
        raise SimulationHandlerArgumentError(str(exc)) from exc


__all__ = ["SIMULATION_NAMES", "SimulationHandlerArgumentError", "dispatch_simulation"]
