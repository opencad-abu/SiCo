"""Bounded structural validation for reuse; legacy graph matching is unchanged."""

from __future__ import annotations

import re

from .circuit_spec_schema import (
    BOOL,
    CircuitSpecError,
    array,
    canonical,
    enum,
    obj,
    unique,
    validate,
)
from .template_schema import NAME, TemplateError

NULL_NAME = {**NAME, "type": ["string", "null"]}
WIDTH = {"type": "integer", "minimum": 1, "maximum": 4096}
PIN = obj({"name": NAME, "net": NULL_NAME})
DEVICE = obj(
    {
        "id": NAME,
        "kind": NAME,
        "role": enum("device", "hierarchy", "unknown"),
        "attributes": {"type": "object", "additionalProperties": True, "maxProperties": 32},
        "pins": array(PIN, 64),
    },
    additionalProperties=True,
)
NET = obj(
    {
        "id": NAME,
        "source_name": NAME,
        "num_bits": WIDTH,
        "is_global": {**BOOL, "type": ["boolean", "null"]},
        "sig_type": NULL_NAME,
    },
    additionalProperties=True,
)
PORT = obj(
    {
        "id": NAME,
        "name": NAME,
        "net": NULL_NAME,
        "direction": {"type": ["string", "null"], "maxLength": 64},
        "num_bits": WIDTH,
    },
    additionalProperties=True,
)
TOPOLOGY = obj(
    {
        "level": enum("direct"),
        "devices": array(DEVICE, 64, 1),
        "nets": array(NET, 256),
        "ports": array(PORT, 128),
        "gaps": array(obj({"code": NAME}, additionalProperties=True), 512),
    },
    additionalProperties=True,
)
MAX_TOPOLOGY_BYTES = 196608
# These capture gaps affect drawing, not the saved electrical graph.
GEOMETRY_GAPS = frozenset({"coordinate_scale_missing", "coordinate_rounded_to_dbu"})


def topology_parts(topology):
    """Validate saved references before graph_data can collapse duplicate node IDs."""
    try:
        if len(canonical(topology).encode()) > MAX_TOPOLOGY_BYTES:
            raise CircuitSpecError("topology exceeds 192 KiB")
        validate(topology, TOPOLOGY, "topology")
        devices = unique(topology["devices"], "id", "devices")
        nets = unique(topology["nets"], "id", "nets")
        ports = unique(topology["ports"], "name", "ports")
        unique(topology["ports"], "id", "port IDs")
        pins = {}
        for key, device in devices.items():
            for name, pin in unique(device["pins"], "name", "device pins").items():
                # graph_data uses ':' as a node delimiter; do not allow collisions.
                if ":" in key or ":" in name:
                    raise CircuitSpecError("device/terminal IDs cannot contain ':'")
                pins[(key, name)] = pin["net"]
        for net in [*pins.values(), *(p["net"] for p in ports.values())]:
            if net is not None and net not in nets:
                raise CircuitSpecError("endpoint references an absent net")
        return devices, nets, ports, pins
    except CircuitSpecError as exc:
        raise TemplateError(str(exc)) from exc


def electrical_issues(topology):
    """Derive object-level evidence gaps for extraction and every reuse consumer."""
    devices, nets, ports, pins = topology_parts(topology)
    issues = []

    def add(code, kind=None, name=None, terminal=None):
        messages = {
            "unsupported_device_class": "A direct electrical device classification is required.",
            "terminal_inventory_unverified": "The saved master has no verified terminals.",
            "bus_not_expanded": "The current reuse workflow requires scalar nets and ports.",
            "unknown_net_scope": "The captured global/local net scope is unknown.",
            "unknown_signal_type": "The captured net signal type is unknown.",
            "unknown_port_direction": "The captured port direction is unsupported or unknown.",
            "unconnected_endpoint": "The saved endpoint has no net; reuse requires a connection.",
            "unknown_source_name": "The source object name is unknown.",
            "bus_or_array_not_expanded": "The source bus/array name requires explicit expansion.",
            "unverified_bus_member_scope": "Recapture scalar bus members with verified net scope.",
            "graph_exceeds_512_nodes": "The complete electrical graph exceeds 512 nodes.",
        }
        action = ("select_supported_source" if code in {
            "bus_not_expanded", "bus_or_array_not_expanded", "graph_exceeds_512_nodes"
        } else "repair_source_and_recapture")
        row = dict(code=code, object_kind=kind, object_ref=name,
                   message=messages[code], next_action=action)
        if terminal is not None:
            row["terminal"] = terminal
        issues.append(row)

    names = []
    for key, device in devices.items():
        name = device.get("source_name", key)
        names.append((name, "device", name, None))
        if device["role"] != "device" or device["kind"] == "unknown":
            add("unsupported_device_class", "device", name)
        if not device["pins"]:
            add("terminal_inventory_unverified", "device", name)
        for pin in device["pins"]:
            names.append((pin["name"], "device", name, pin["name"]))
            if pin["net"] is None:
                add("unconnected_endpoint", "device", name, pin["name"])
    for net in nets.values():
        name = net["source_name"]
        names.append((name, "net", name, None))
        if net["num_bits"] != 1:
            add("bus_not_expanded", "net", name)
        if net["is_global"] is None:
            add("unknown_net_scope", "net", name)
        if net["sig_type"] not in {"signal", "supply", "ground"}:
            add("unknown_signal_type", "net", name)
    for port in ports.values():
        name = port["name"]
        names.append((name, "port", name, None))
        if port["num_bits"] != 1:
            add("bus_not_expanded", "port", name)
        if port["direction"] not in {"input", "output", "inputOutput"}:
            add("unknown_port_direction", "port", name)
        if port["net"] is None:
            add("unconnected_endpoint", "port", name)
    for name, kind, reference, terminal in names:
        if not isinstance(name, str):
            add("unknown_source_name", kind, reference, terminal)
        elif any(c in name for c in "<>[]"):
            if not re.fullmatch(r"[^<>\[\]]+<[0-9]+>!?", name):
                add("bus_or_array_not_expanded", kind, reference, terminal)
            elif topology.get("net_global_semantics") != "uniform_boolean_v1":
                add("unverified_bus_member_scope", kind, reference, terminal)
    if len(devices) + len(nets) + len(ports) + len(pins) > 512:
        add("graph_exceeds_512_nodes")
    return issues


def evidence_gaps(topology):
    """Unknown facts remain unknown even if two saved graphs happen to be identical."""
    issues = electrical_issues(topology)
    gaps = {g["code"] for g in topology["gaps"]} - GEOMETRY_GAPS
    return sorted(gaps | {row["code"] for row in issues})
