"""Contracts and validation for the latched comparator model class."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..model import ModelGenerationRequest

_REQUIRED_ROLES = {
    "positive_output",
    "negative_output",
    "positive_input",
    "negative_input",
    "clock",
}


@dataclass(frozen=True)
class BaselineGenerationRequest(ModelGenerationRequest):
    """Backward-compatible comparator alias for the generic request contract."""


@dataclass(frozen=True)
class BaselineCandidate:
    source: str
    smoke_testbench: str
    model_version: str
    module: str
    test_plan: Mapping[str, Any] | None = None
    # Optional model-class-owned hand-off for a registered Xcelium evidence
    # adapter. The legacy baseline remains unbound when no adapter is selected.
    xcelium_evidence_binding: Mapping[str, Mapping[str, Any]] | None = None
    xcelium_evidence_adapter: str | None = None


def _validate(spec: Mapping[str, Any], contract: Mapping[str, Any]) -> None:
    if spec.get("schema_version") != 1:
        raise ValueError("unsupported RNM supplement schema")
    target = spec.get("target")
    if not isinstance(target, Mapping) or target.get("cell") != contract["target"]["cell"]:
        raise ValueError("RNM supplement target does not match interface contract")
    interface = spec.get("interface")
    if not isinstance(interface, Mapping):
        raise ValueError("RNM supplement must define an interface")
    ports = interface.get("ports")
    order = interface.get("port_order")
    roles = interface.get("roles")
    if not isinstance(ports, Mapping) or not isinstance(order, list) or not isinstance(roles, Mapping):
        raise ValueError("RNM supplement must define ports, port_order, and semantic roles")
    contract_order = contract["interface"]["port_order"]
    if order != contract_order or set(ports) != set(contract_order):
        raise ValueError("RNM supplement interface does not match interface contract")
    if set(roles) != _REQUIRED_ROLES or any(value not in ports for value in roles.values()):
        raise ValueError("latched comparator semantic roles are incomplete or reference unknown ports")
    for name in order:
        item = ports[name]
        if not isinstance(item, Mapping) or not item.get("direction") or not item.get("kind"):
            raise ValueError(f"RNM port {name!r} is missing direction or kind")
    if ports[roles["clock"]].get("kind") != "digital":
        raise ValueError("latched comparator clock role must reference a digital port")
    parameters = spec.get("parameters")
    if not isinstance(parameters, Mapping) or not parameters:
        raise ValueError("RNM supplement must define behavior parameters")
    if spec.get("status") != "assumption_pending_spectre_correlation":
        raise ValueError("RNM supplement status must explicitly require Spectre correlation")


__all__ = ["BaselineCandidate", "BaselineGenerationRequest", "_validate"]
