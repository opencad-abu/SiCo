"""Compare full candidate hierarchies against authoritative SI structures."""

from __future__ import annotations
from typing import Mapping, Sequence
from .connectivity_model import (
    ConnectivityParseError,
    Finding,
    Module,
    Port,
    Structure,
)


from .connectivity_normalize import canonicalize_module, apply_aliases


def compare_structures(
    official: Structure,
    candidate: Structure,
    *,
    expected_module: str,
    expected_ports: Sequence[str] | None = None,
    expected_globals: Sequence[tuple[str, str]] | None = None,
    module_aliases: Mapping[str, Mapping[str, str]] | None = None,
) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    official_by_name = {m.name: m for m in official.modules}
    candidate_by_name = {m.name: m for m in candidate.modules}
    top = official_by_name.get(expected_module)
    candidate_top = candidate_by_name.get(expected_module)
    if top is None:
        return (
            Finding(
                "official_top_missing",
                "error",
                "top",
                "official top module is absent",
                expected_module,
            ),
        )
    if candidate_top is None:
        return (
            Finding(
                "candidate_top_missing",
                "error",
                "top",
                "candidate top module is absent",
                expected_module,
            ),
        )
    expected_order = tuple(expected_ports or (port.name for port in top.ports))
    scoped_aliases = module_aliases or {}
    alias_map = dict(official.aliases)
    alias_map.update(scoped_aliases.get(expected_module, {}))
    candidate_top = apply_aliases(candidate_top, alias_map)
    actual_order = tuple(port.name for port in candidate_top.ports)
    if actual_order != expected_order:
        findings.append(
            Finding(
                "port_order_mismatch",
                "error",
                "top",
                "candidate top port order differs",
                expected_order,
                actual_order,
            )
        )
    _compare_directions(findings, top, candidate_top, "top", "top")
    official_children = {m.name: m.ports for m in official.modules}
    candidate_children = dict(official_children)
    candidate_children.update({m.name: m.ports for m in candidate.modules})
    try:
        official_canonical = canonicalize_module(top, official_children)
        candidate_canonical = canonicalize_module(candidate_top, candidate_children)
    except ConnectivityParseError as exc:
        return tuple(
            findings + [Finding("connection_parse_ambiguous", "error", "top", str(exc))]
        )
    findings.extend(_compare_bindings(official_canonical, candidate_canonical, "top"))
    if expected_globals is not None:
        expected_set = set(expected_globals)
        actual_set = set(candidate.globals)
        findings.extend(
            Finding(
                "global_mapping_missing",
                "error",
                "global",
                "candidate global mapping is missing",
                item,
                None,
            )
            for item in sorted(expected_set - actual_set)
        )
        findings.extend(
            Finding(
                "global_mapping_extra",
                "error",
                "global",
                "candidate contains an unexpected global mapping",
                None,
                item,
            )
            for item in sorted(actual_set - expected_set)
        )
    for name in sorted(set(official_by_name) - set(candidate_by_name)):
        if name != expected_module:
            findings.append(
                Finding(
                    "missing_module",
                    "error",
                    "hierarchy",
                    f"candidate omits module {name}",
                    name,
                    None,
                )
            )
    for name in sorted(set(candidate_by_name) - set(official_by_name)):
        if name != expected_module:
            findings.append(
                Finding(
                    "extra_module",
                    "error",
                    "hierarchy",
                    f"candidate adds module {name}",
                    None,
                    name,
                )
            )
    for name in sorted(set(official_by_name) & set(candidate_by_name)):
        if name == expected_module:
            continue
        expected_child = official_by_name[name]
        actual_child = candidate_by_name[name]
        expected_order_child = tuple(p.name for p in expected_child.ports)
        actual_order_child = tuple(p.name for p in actual_child.ports)
        if actual_order_child != expected_order_child:
            findings.append(
                Finding(
                    "module_port_order_mismatch",
                    "error",
                    name,
                    f"candidate module {name} port order differs",
                    expected_order_child,
                    actual_order_child,
                )
            )
        _compare_directions(
            findings, expected_child, actual_child, name, f"module {name}"
        )
        try:
            expected_c = canonicalize_module(expected_child, official_children)
            actual_c = canonicalize_module(
                apply_aliases(actual_child, scoped_aliases.get(name, {})),
                candidate_children,
            )
        except ConnectivityParseError as exc:
            findings.append(
                Finding("connection_parse_ambiguous", "error", name, str(exc))
            )
            continue
        findings.extend(_compare_bindings(expected_c, actual_c, name))
    findings.extend(_multiple_driver_findings(candidate_top, candidate_children))
    for name, module in candidate_by_name.items():
        if name != expected_module:
            findings.extend(_multiple_driver_findings(module, candidate_children))
    return tuple(findings)


def _compare_directions(
    findings: list[Finding], expected: Module, actual: Module, scope: str, subject: str
) -> None:
    expected_directions = {p.name: p.direction for p in expected.ports}
    actual_directions = {p.name: p.direction for p in actual.ports}
    for port in sorted(set(expected_directions) & set(actual_directions)):
        if expected_directions[port] != actual_directions[port]:
            findings.append(
                Finding(
                    "port_direction_mismatch",
                    "error",
                    scope,
                    f"candidate {subject} port {port} direction differs",
                    expected_directions[port],
                    actual_directions[port],
                    terminal=port,
                )
            )


def _compare_bindings(
    expected: Mapping[str, object], actual: Mapping[str, object], scope: str
) -> list[Finding]:
    findings: list[Finding] = []
    expected_instances = {i["name"]: i for i in expected["instances"]}
    actual_instances = {i["name"]: i for i in actual["instances"]}
    for name in sorted(set(expected_instances) - set(actual_instances)):
        findings.append(
            Finding(
                "missing_instance",
                "error",
                scope,
                f"candidate omits instance {name}",
                expected_instances[name],
                None,
                name,
            )
        )
    for name in sorted(set(actual_instances) - set(expected_instances)):
        findings.append(
            Finding(
                "extra_instance",
                "error",
                scope,
                f"candidate adds instance {name}",
                None,
                actual_instances[name],
                name,
            )
        )
    for name in sorted(set(expected_instances) & set(actual_instances)):
        e = expected_instances[name]
        a = actual_instances[name]
        if e["master"] != a["master"]:
            findings.append(
                Finding(
                    "instance_master_mismatch",
                    "error",
                    scope,
                    f"instance {name} master differs",
                    e["master"],
                    a["master"],
                    name,
                )
            )
        ec = dict(e["connections"])
        ac = dict(a["connections"])
        for terminal in sorted(set(ec) | set(ac)):
            if ec.get(terminal) != ac.get(terminal):
                findings.append(
                    Finding(
                        "connection_mismatch",
                        "error",
                        scope,
                        f"terminal {terminal} on {name} is connected differently",
                        ec.get(terminal),
                        ac.get(terminal),
                        name,
                        terminal,
                    )
                )
    return findings


def _multiple_driver_findings(
    module: Module, children: Mapping[str, Sequence[Port]]
) -> list[Finding]:
    drivers: dict[str, list[str]] = {}
    for instance in module.instances:
        ports = children.get(instance.master, ())
        for terminal, net in instance.connections:
            if terminal.isdigit():
                index = int(terminal)
                if index >= len(ports):
                    continue
                port = ports[index]
            else:
                port = next((item for item in ports if item.name == terminal), None)
            if port and port.direction == "output":
                drivers.setdefault(net, []).append(instance.name)
    return [
        Finding(
            "multiple_drivers",
            "error",
            "top",
            f"net {net} has multiple output drivers",
            expected=1,
            actual=len(names),
        )
        for net, names in sorted(drivers.items())
        if len(names) > 1
    ]
