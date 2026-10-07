"""Canonical, fail-closed domain contract for the M1 LDO slice.

This module deliberately contains no EDA integration.  It turns a small,
JSON-compatible interface description into an immutable canonical IR and
matches only the two supported LDO topologies.  The matcher is intentionally
stricter than the generic template provider: port names, order, direction,
kind and optional-enable semantics must all agree exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from types import MappingProxyType
from typing import Any, Mapping, Sequence


class LDOContractError(ValueError):
    """Raised when an LDO contract or structure is malformed."""


class LDOTopologyError(LDOContractError):
    """Raised when a structure does not match a supported topology."""


_TOPOLOGIES = {
    "LDO_MASTER": (
        ("VDD", "inout", "power"),
        ("VSS", "inout", "ground"),
        ("VOUT", "output", "power"),
    ),
    "LDO_AON": (
        ("VDD", "inout", "power"),
        ("VSS", "inout", "ground"),
        ("EN", "input", "digital"),
        ("VOUT", "output", "power"),
    ),
}


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise LDOContractError("%s must be non-empty text" % label)
    return value


@dataclass(frozen=True)
class LDOPort:
    name: str
    direction: str
    kind: str
    optional: bool = False
    unit: str = "V"

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "name": self.name,
            "direction": self.direction,
            "kind": self.kind,
            "unit": self.unit,
        }
        if self.optional:
            result["optional"] = True
        return result


@dataclass(frozen=True)
class LDOInterface:
    cell: str
    topology: str
    ports: tuple[LDOPort, ...]
    source: str = "contract"

    @property
    def port_order(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.ports)

    @property
    def port_map(self) -> Mapping[str, LDOPort]:
        return MappingProxyType({item.name: item for item in self.ports})

    def to_dict(self) -> dict[str, object]:
        return {
            "cell": self.cell,
            "topology": self.topology,
            "port_order": list(self.port_order),
            "ports": [item.to_dict() for item in self.ports],
            "source": self.source,
        }

    @property
    def digest(self) -> str:
        encoded = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LDOStructure:
    """Small canonical structure summary consumed by the matcher."""

    cell: str
    ports: tuple[LDOPort, ...]
    topology: str | None = None
    device_counts: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    source_generation: str | None = None

    @property
    def port_order(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.ports)

    def to_dict(self) -> dict[str, object]:
        return {
            "cell": self.cell,
            "port_order": [item.name for item in self.ports],
            "ports": [item.to_dict() for item in self.ports],
            "topology": self.topology,
            "device_counts": dict(self.device_counts),
            "source_generation": self.source_generation,
        }


@dataclass(frozen=True)
class LDOMatch:
    interface: LDOInterface
    structure: LDOStructure
    score: int
    reasons: tuple[str, ...]

    @property
    def matched(self) -> bool:
        return not any(reason.startswith("mismatch:") for reason in self.reasons)

    def to_dict(self) -> dict[str, object]:
        return {
            "matched": self.matched,
            "topology": self.interface.topology,
            "cell": self.interface.cell,
            "score": self.score,
            "reasons": list(self.reasons),
            "interface_digest": self.interface.digest,
        }


def supported_topologies() -> tuple[str, ...]:
    return tuple(_TOPOLOGIES)


def topology_for_cell(cell: str) -> str:
    if cell not in _TOPOLOGIES:
        raise LDOTopologyError("unsupported LDO topology/cell: %s" % cell)
    return cell


def _validate_semantics(interface: LDOInterface) -> None:
    expected = _TOPOLOGIES[interface.cell]
    if interface.source == "oa-cdf-interface-v1":
        expected = tuple((name, "input" if name in {"VDD", "VSS"} else direction, kind)
                         for name, direction, kind in expected)
    if len(interface.ports) != len(expected):
        raise LDOContractError("%s must declare exactly %d ports" % (interface.cell, len(expected)))
    for actual, wanted in zip(interface.ports, expected):
        if (actual.name, actual.direction, actual.kind) != wanted:
            raise LDOContractError(
                "unsupported %s port semantics for %s: got=%s expected=%s"
                % (interface.cell, actual.name, (actual.name, actual.direction, actual.kind), wanted)
            )
    if interface.cell == "LDO_MASTER" and any(item.optional for item in interface.ports):
        raise LDOContractError("LDO_MASTER has no optional ports")
    if interface.cell == "LDO_AON" and interface.port_map["EN"].optional:
        raise LDOContractError("LDO_AON EN is required, not optional")


def canonicalize_interface(
    value: Mapping[str, Any],
    *,
    cell: str | None = None,
    _enforce_semantics: bool = True,
) -> LDOInterface:
    """Parse either list- or map-shaped ports without mutating ``value``."""
    if not isinstance(value, Mapping):
        raise LDOContractError("interface contract must be an object")
    # A family manifest may carry several explicitly named variants.  Select
    # one before parsing; never merge ports from different variants.
    variants = value.get("variants")
    if isinstance(variants, Mapping):
        selected_cell = cell
        if selected_cell is None:
            target = value.get("target")
            if isinstance(target, Mapping):
                selected_cell = target.get("cell")
            elif isinstance(target, str):
                selected_cell = target
        if not isinstance(selected_cell, str) or selected_cell not in variants:
            raise LDOContractError("interface family requires a supported variant cell")
        selected = variants.get(selected_cell)
        if not isinstance(selected, Mapping):
            raise LDOContractError("interface variant %s must be an object" % selected_cell)
        value = dict(selected)
        value.setdefault("cell", selected_cell)
        if "source" in value:
            value["source"] = str(value["source"])

    raw_target = value.get("target", {})
    target_cell = cell or value.get("cell") or (
        raw_target.get("cell") if isinstance(raw_target, Mapping) else None
    )
    target_cell = _text(target_cell, "interface.cell")
    topology = topology_for_cell(target_cell)
    raw_ports = value.get("ports")
    order = value.get("port_order")
    if isinstance(raw_ports, Mapping):
        if order is None:
            order = list(raw_ports)
        else:
            # Preserve undeclared mapping keys in a deterministic suffix so a
            # structure with an extra terminal becomes a match failure rather
            # than silently disappearing during canonicalization.
            declared = set(order)
            extras = sorted(str(name) for name in raw_ports if name not in declared)
            if extras:
                order = list(order) + extras
        items = []
        for name in order:
            if name not in raw_ports:
                if _enforce_semantics:
                    raise LDOContractError("interface.port_order references missing port %r" % name)
                items.append({"name": name, "direction": "", "kind": ""})
                continue
            declaration = raw_ports[name]
            if not isinstance(declaration, Mapping):
                if _enforce_semantics:
                    raise LDOContractError("port %s must be an object" % name)
                items.append({"name": name, "direction": "", "kind": ""})
                continue
            item = dict(declaration)
            item["name"] = name
            items.append(item)
    elif isinstance(raw_ports, Sequence) and not isinstance(raw_ports, (str, bytes)):
        items = list(raw_ports)
        inferred_order = [item.get("name") if isinstance(item, Mapping) else None for item in items]
        if order is None:
            # A list is already an ordered representation.  Infer its order so
            # structure summaries without a redundant ``port_order`` field can
            # still be compared and report semantic mismatches deterministically.
            order = inferred_order
        elif list(order) != inferred_order:
            if _enforce_semantics:
                raise LDOContractError("interface.port_order does not match ports order")
    else:
        raise LDOContractError("interface.ports must be an object or array")
    if not isinstance(order, Sequence) or isinstance(order, (str, bytes)):
        raise LDOContractError("interface.port_order must be an array")
    if any(not isinstance(name, str) or not name for name in order):
        raise LDOContractError("interface.port_order must contain non-empty strings")
    if len(items) != len(order) or len(set(order)) != len(order):
        raise LDOContractError("interface port order is malformed")
    ports: list[LDOPort] = []
    for index, raw in enumerate(items):
        if not isinstance(raw, Mapping):
            if _enforce_semantics:
                raise LDOContractError("interface port %d must be an object" % index)
            raw = {}
        raw_name = raw.get("name", order[index] if index < len(order) else "")
        if isinstance(raw_name, str) and raw_name:
            name = raw_name
        elif _enforce_semantics:
            raise LDOContractError("port.name must be non-empty text")
        else:
            name = str(order[index]) if index < len(order) else ""
        if name != order[index]:
            if _enforce_semantics:
                raise LDOContractError("interface port order is not canonical")
        raw_direction = raw.get("direction")
        raw_kind = raw.get("kind")
        if isinstance(raw_direction, str) and raw_direction:
            direction = raw_direction
        elif _enforce_semantics:
            raise LDOContractError("port.%s.direction must be non-empty text" % name)
        else:
            direction = ""
        if isinstance(raw_kind, str) and raw_kind:
            kind = raw_kind
        elif _enforce_semantics:
            raise LDOContractError("port.%s.kind must be non-empty text" % name)
        else:
            kind = ""
        optional = raw.get("optional", False)
        if not isinstance(optional, bool):
            if _enforce_semantics:
                raise LDOContractError("port.%s.optional must be boolean" % name)
            optional = False
        unit = raw.get("unit", "V")
        if not isinstance(unit, str) or not unit:
            if _enforce_semantics:
                raise LDOContractError("port.%s.unit must be non-empty text" % name)
            unit = ""
        ports.append(LDOPort(name, direction, kind, optional, unit))
    result = LDOInterface(target_cell, topology, tuple(ports), str(value.get("source", "contract")))
    if _enforce_semantics:
        _validate_semantics(result)
    return result


def canonicalize_structure(
    value: Mapping[str, Any],
    *,
    cell: str | None = None,
    strict: bool = True,
) -> LDOStructure:
    if not isinstance(value, Mapping):
        raise LDOContractError("structure must be an object")
    raw_cell = value.get("cell") if value.get("cell") is not None else cell
    target_cell = _text(raw_cell, "structure.cell")
    if target_cell not in _TOPOLOGIES:
        if strict:
            raise LDOTopologyError("unsupported LDO topology/cell: %s" % target_cell)
    raw_ports = value.get("ports", value.get("interface_ports"))
    interface_value: dict[str, Any] = {"cell": target_cell, "ports": raw_ports}
    if value.get("port_order") is not None:
        interface_value["port_order"] = value.get("port_order")
    # Structures are intentionally parsed without applying the expected
    # topology's semantic rules.  The comparison layer must be able to report
    # wrong direction/kind/order/extra ports as a deterministic mismatch.
    interface = canonicalize_interface(
        interface_value,
        cell=target_cell,
        _enforce_semantics=False,
    )
    counts = value.get("device_counts", {})
    if not isinstance(counts, Mapping):
        raise LDOContractError("structure.device_counts must be an object")
    normalized_counts: dict[str, int] = {}
    for name, count in counts.items():
        if not isinstance(name, str) or isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise LDOContractError("structure.device_counts contains an invalid count")
        normalized_counts[name] = count
    topology = value.get("topology")
    if isinstance(topology, Mapping):
        topology = topology.get("cell", topology.get("kind"))
    if topology is None or topology == "linear_regulator":
        normalized_topology = target_cell
    else:
        normalized_topology = str(topology)
    if strict and normalized_topology not in (target_cell,):
        raise LDOTopologyError("structure topology does not match cell")
    return LDOStructure(
        target_cell,
        interface.ports,
        normalized_topology,
        MappingProxyType(normalized_counts),
        value.get("source_generation"),
    )


def match_interface(contract: LDOInterface | Mapping[str, Any], structure: LDOStructure | Mapping[str, Any]) -> LDOMatch:
    expected = contract if isinstance(contract, LDOInterface) else canonicalize_interface(contract)
    malformed_reason: str | None = None
    if isinstance(structure, LDOStructure):
        actual = structure
    else:
        try:
            actual = canonicalize_structure(structure, cell=expected.cell, strict=False)
        except (LDOContractError, TypeError, ValueError) as exc:
            malformed_reason = str(exc)
            raw_cell = structure.get("cell") if isinstance(structure, Mapping) else expected.cell
            actual_cell = raw_cell if isinstance(raw_cell, str) and raw_cell else expected.cell
            raw_topology = structure.get("topology") if isinstance(structure, Mapping) else None
            if isinstance(raw_topology, Mapping):
                raw_topology = raw_topology.get("cell", raw_topology.get("kind"))
            actual = LDOStructure(
                actual_cell,
                (),
                str(raw_topology or actual_cell),
                MappingProxyType({}),
                None,
            )
    reasons: list[str] = []
    if malformed_reason:
        reasons.append("mismatch:malformed_structure:%s" % malformed_reason)
    if actual.cell != expected.cell:
        reasons.append("mismatch:cell")
    if actual.topology != expected.topology:
        reasons.append("mismatch:topology")
    if actual.port_order != expected.port_order:
        reasons.append("mismatch:port_order")
    if len(actual.ports) != len(expected.ports):
        reasons.append("mismatch:port_set")
    for index, wanted in enumerate(expected.ports):
        if index >= len(actual.ports):
            reasons.append("mismatch:missing:%s" % wanted.name)
            continue
        got = actual.ports[index]
        if got.name != wanted.name:
            reasons.append("mismatch:port_name:%s" % wanted.name)
        if got.direction != wanted.direction:
            reasons.append("mismatch:direction:%s" % wanted.name)
        if got.kind != wanted.kind:
            reasons.append("mismatch:kind:%s" % wanted.name)
        if got.optional != wanted.optional:
            reasons.append("mismatch:optional:%s" % wanted.name)
        if got.unit != wanted.unit:
            reasons.append("mismatch:unit:%s" % wanted.name)
    expected_names = {item.name for item in expected.ports}
    actual_names = {item.name for item in actual.ports}
    for name in sorted(actual_names - expected_names):
        reasons.append("mismatch:extra:%s" % name)
    score = max(0, 100 - 10 * len(reasons))
    return LDOMatch(expected, actual, score, tuple(reasons))


def require_match(contract: LDOInterface | Mapping[str, Any], structure: LDOStructure | Mapping[str, Any]) -> LDOMatch:
    match = match_interface(contract, structure)
    if not match.matched:
        raise LDOTopologyError("LDO interface mismatch: %s" % ",".join(match.reasons))
    return match


def load_contract(value: Mapping[str, Any], *, cell: str | None = None) -> LDOInterface:
    """Alias used by plugins and callers that already loaded JSON."""
    return canonicalize_interface(value, cell=cell)


__all__ = [
    "LDOContractError", "LDOTopologyError", "LDOPort", "LDOInterface",
    "LDOStructure", "LDOMatch", "supported_topologies", "topology_for_cell",
    "canonicalize_interface", "canonicalize_structure", "match_interface",
    "require_match", "load_contract",
]
