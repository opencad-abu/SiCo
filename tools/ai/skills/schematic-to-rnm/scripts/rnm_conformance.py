"""RNM port, spec and modeling-style checks over parsed module facts."""

from __future__ import annotations

import re

from rnm_findings import Finding
from rnm_module import ModuleFacts, strip_comments

REAL_NET_TYPES = {
    "wreal",
    "wreal1driver",
    "wreal4state",
    "wrealsum",
    "wrealmin",
    "wrealmax",
    "wrealavg",
}


def check_ports(module: ModuleFacts, netlist_ports: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    module_ports = module.port_names()

    module_set = {name.casefold(): name for name in module_ports}
    netlist_set = {name.casefold(): name for name in netlist_ports}

    for key, original in netlist_set.items():
        if key not in module_set:
            findings.append(
                Finding(
                    "error",
                    "port-missing",
                    original,
                    "netlist port is absent from the module header",
                )
            )
    for key, original in module_set.items():
        if key not in netlist_set:
            findings.append(
                Finding(
                    "error",
                    "port-extra",
                    original,
                    "module port has no counterpart in the netlist",
                )
            )

    common = [name for name in netlist_ports if name.casefold() in module_set]
    if len(common) == len(netlist_ports) == len(module_ports):
        module_order = [name.casefold() for name in module_ports]
        netlist_order = [name.casefold() for name in netlist_ports]
        if module_order != netlist_order:
            findings.append(
                Finding(
                    "warning",
                    "port-order",
                    module.name,
                    "port order differs from the netlist; positional "
                    "instantiation would mis-connect",
                )
            )

    for name in module_ports:
        if name not in module.directions:
            findings.append(
                Finding(
                    "error",
                    "port-direction",
                    name,
                    "port has no input/output/inout declaration",
                )
            )
    return findings


def check_spec(module: ModuleFacts, spec: dict) -> list[Finding]:
    findings: list[Finding] = []

    cell = spec.get("cell") or spec.get("block", {}).get("cell")
    if cell and module.name.casefold() != str(cell).casefold():
        findings.append(
            Finding(
                "warning",
                "spec-cell",
                module.name,
                f"module name differs from spec cell {cell!r}",
            )
        )

    spec_ports = spec.get("ports") or {}
    for name, attributes in spec_ports.items():
        if not isinstance(attributes, dict):
            continue
        key = name.casefold()
        actual_direction = next(
            (
                direction
                for port, direction in module.directions.items()
                if port.casefold() == key
            ),
            None,
        )
        expected_direction = attributes.get("direction")
        if expected_direction and actual_direction and expected_direction != actual_direction:
            findings.append(
                Finding(
                    "error",
                    "spec-direction",
                    name,
                    f"spec declares {expected_direction}, module declares {actual_direction}",
                )
            )

        actual_type = next(
            (
                net_type
                for port, net_type in module.net_types.items()
                if port.casefold() == key
            ),
            None,
        )
        kind = str(attributes.get("kind", "")).casefold()
        if kind in {"power", "ground", "analog"}:
            if actual_type is None:
                findings.append(
                    Finding(
                        "error",
                        "net-type",
                        name,
                        f"{kind} port needs an explicit real net type (wreal family)",
                    )
                )
            elif actual_type not in REAL_NET_TYPES:
                findings.append(
                    Finding(
                        "error",
                        "net-type",
                        name,
                        f"{kind} port is declared {actual_type}; expected a wreal type",
                    )
                )
        elif kind == "digital" and actual_type in REAL_NET_TYPES:
            findings.append(
                Finding(
                    "warning",
                    "net-type",
                    name,
                    f"digital port is declared {actual_type}; expected logic/wire",
                )
            )

    metrics = spec.get("metrics") or {}
    if metrics:
        declared = {name.casefold() for name in module.parameters}
        for metric in metrics:
            if metric.casefold() not in declared:
                findings.append(
                    Finding(
                        "info",
                        "spec-metric",
                        metric,
                        "spec metric is not exposed as a module parameter",
                    )
                )
    return findings


def check_modeling_style(module: ModuleFacts, text: str) -> list[Finding]:
    findings: list[Finding] = []
    clean = strip_comments(text)

    real_ports = [
        name
        for name, net_type in module.net_types.items()
        if net_type in REAL_NET_TYPES and name in set(module.port_names())
    ]
    if not real_ports:
        findings.append(
            Finding(
                "error",
                "style",
                module.name,
                "no port uses a wreal type; this is not a real-number model",
            )
        )

    if "wreal4state" in clean and "cds_rnm_pkg" not in clean:
        findings.append(
            Finding(
                "error",
                "style",
                module.name,
                "wreal4state requires 'import cds_rnm_pkg::*;'",
            )
        )

    if re.search(r"\b(nmos|pmos|cmos|tran|rtran)\s+\w*\s*\(", clean):
        findings.append(
            Finding(
                "error",
                "style",
                module.name,
                "switch primitives found; a behavioral RNM must not be structural",
            )
        )

    if not re.search(r"\b(always|assign|always_comb|always_ff)\b", clean):
        findings.append(
            Finding(
                "error",
                "style",
                module.name,
                "no always block or continuous assignment; module has no behavior",
            )
        )

    if not re.search(r"`timescale", clean):
        findings.append(
            Finding("warning", "style", module.name, "no `timescale directive")
        )

    outputs = [
        name
        for name in module.port_names()
        if module.directions.get(name) == "output"
    ]
    for name in outputs:
        driven = re.search(
            rf"\bassign\s+{re.escape(name)}\b|\b{re.escape(name)}\s*(?:<=|=)",
            clean,
        )
        if not driven:
            findings.append(
                Finding("error", "undriven", name, "output port is never assigned")
            )
    return findings
