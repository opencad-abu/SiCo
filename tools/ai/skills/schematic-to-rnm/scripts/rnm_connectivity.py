"""Cross-check schematic terminals against the instance and reverse net indexes."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from rnm_inspection import (
    REQUIRED_SCHEMATIC_KEYS,
    BlockedInput,
    _as_dicts,
    _cellview,
    _integer,
    _require_ok,
    _text,
)


def _is_schematic_pin_marker(lib: str, cell: str) -> bool:
    """Identify the standard graphical pin instances represented as OA insts.

    Virtuoso schematic terminals created with the built-in pin commands also
    appear in ``cv~>instances`` as ``basic/ipin``, ``basic/opin`` or
    ``basic/iopin``.  They are editor markers, not hierarchy instances in the
    simulator netlist; their connectivity is already carried by the top-level
    terminal/pin records.
    """

    return lib.casefold() == "basic" and cell.casefold() in {"ipin", "opin", "iopin"}


def _normalize_schematic(
    schematic: Mapping[str, Any], path: Path, symbol_ports: list[dict[str, Any]]
) -> dict[str, Any]:
    _require_ok(schematic, "schematic", REQUIRED_SCHEMATIC_KEYS, path)
    identity = _cellview(schematic, str(path))
    instances = _as_dicts(schematic.get("instances"), "instances", str(path))
    nets = _as_dicts(schematic.get("nets"), "nets", str(path))
    terminals = _as_dicts(schematic.get("terminals"), "terminals", str(path))

    symbol_by_name = {str(item["name"]).casefold(): item for item in symbol_ports}
    instance_entries: list[dict[str, Any]] = []
    ignored_instances: list[dict[str, str]] = []
    all_connections: list[dict[str, Any]] = []
    instance_names: set[str] = set()
    instance_terminal_names: set[tuple[str, str]] = set()
    for instance in instances:
        subject = f"{path}: instance {instance.get('name')!r}"
        name = _text(instance.get("name"), "name", subject, required=True)
        name_key = name.casefold()
        if name_key in instance_names:
            raise BlockedInput(f"{path}: duplicate instance {name!r}", code="duplicate_name")
        instance_names.add(name_key)
        lib = _text(instance.get("lib"), "lib", subject, required=True)
        cell = _text(instance.get("cell"), "cell", subject, required=True)
        view = _text(instance.get("view"), "view", subject, required=True)
        is_pin_marker = _is_schematic_pin_marker(lib, cell)
        raw_terms = instance.get("terminals")
        if not isinstance(raw_terms, list):
            raise BlockedInput(
                f"{subject}: complete terminals list is required; old MCP output is insufficient",
                code="missing_terminal_connections",
            )
        term_entries = _as_dicts(raw_terms, "terminals", subject)
        normalized_terms: list[dict[str, Any]] = []
        term_names: set[str] = set()
        for term in term_entries:
            term_subject = f"{subject} terminal {term.get('name')!r}"
            term_name = _text(term.get("name"), "name", term_subject, required=True)
            term_key = term_name.casefold()
            if term_key in term_names:
                raise BlockedInput(f"{subject}: duplicate terminal {term_name!r}", code="duplicate_name")
            term_names.add(term_key)
            if not is_pin_marker:
                instance_terminal_names.add((name_key, term_key))
            if "net" not in term:
                raise BlockedInput(
                    f"{term_subject}: net field is required (null means explicitly unconnected)",
                    code="missing_terminal_connections",
                )
            net = _text(term.get("net"), "net", term_subject)
            normalized_term = {
                "name": term_name,
                "net": net,
                "direction": _text(term.get("direction"), "direction", term_subject),
                "data_type": _text(term.get("data_type"), "data_type", term_subject),
                "port_kind": _text(term.get("port_kind"), "port_kind", term_subject),
            }
            normalized_terms.append(normalized_term)
            if not is_pin_marker:
                all_connections.append({"instance": name, "terminal": term_name, "net": net})
        expected_count = _integer(instance.get("term_count"), "term_count", subject)
        if expected_count is not None and expected_count != len(normalized_terms):
            raise BlockedInput(
                f"{subject}: term_count={expected_count} but terminals has {len(normalized_terms)}",
                code="count_mismatch",
            )
        if is_pin_marker:
            ignored_instances.append(
                {
                    "name": name,
                    "master": f"{lib}/{cell}",
                    "reason": "virtuoso_top_level_pin_marker",
                }
            )
            continue
        instance_entries.append(
            {
                "path": name,
                "name": name,
                "master": f"{lib}/{cell}",
                "lib": lib,
                "cell": cell,
                "view": view,
                "terminals": normalized_terms,
                "term_count": len(normalized_terms),
            }
        )

    net_entries: list[dict[str, Any]] = []
    net_names: set[str] = set()
    declared_net_connections: set[tuple[str, str, str | None]] = set()
    for net in nets:
        subject = f"{path}: net {net.get('name')!r}"
        name = _text(net.get("name"), "name", subject, required=True)
        key = name.casefold()
        if key in net_names:
            raise BlockedInput(f"{path}: duplicate net {name!r}", code="duplicate_name")
        net_names.add(key)
        raw_connections = net.get("connections")
        if raw_connections is not None and not isinstance(raw_connections, list):
            raise BlockedInput(f"{subject}: connections must be a list", code="invalid_field")
        if raw_connections is None:
            # The current read-only SKILL export carries the authoritative
            # direction instance-terminal -> net.  Build the reverse index
            # deterministically instead of duplicating it in the MCP payload.
            raw_connections = [
                {
                    "instance": connection["instance"],
                    "terminal": connection["terminal"],
                    "net": connection["net"],
                }
                for connection in all_connections
                if connection["instance"] is not None and connection["net"] == name
            ]
        connections: list[dict[str, Any]] = []
        for connection in _as_dicts(raw_connections, "connections", subject):
            instance_name = _text(connection.get("instance"), "instance", subject)
            terminal_name = _text(
                connection.get("terminal", connection.get("name")),
                "terminal",
                subject,
                required=True,
            )
            connection_net = _text(connection.get("net"), "net", subject)
            if connection_net != name:
                raise BlockedInput(
                    f"{subject}: connection {instance_name}.{terminal_name} refers to {connection_net!r}",
                    code="connection_mismatch",
                )
            if instance_name is None:
                raise BlockedInput(
                    f"{subject}: instance name is required for instance-terminal connections",
                    code="missing_field",
                )
            item = {"instance": instance_name, "terminal": terminal_name, "net": name}
            connections.append(item)
            declared_net_connections.add(
                (instance_name.casefold(), terminal_name.casefold(), name.casefold())
            )
        expected_connection_count = _integer(
            net.get("instance_terminal_count"), "instance_terminal_count", subject
        )
        if expected_connection_count is not None and expected_connection_count != len(connections):
            raise BlockedInput(
                f"{subject}: instance_terminal_count={expected_connection_count} "
                f"but connections has {len(connections)}",
                code="count_mismatch",
            )
        net_entries.append(
            {
                "name": name,
                "bits": _integer(net.get("bits"), "bits", subject),
                "signal_type": _text(net.get("signal_type"), "signal_type", subject),
                "kind": "global" if net.get("global") is True else "signal",
                "global": net.get("global") is True,
                "connections": connections,
                "instance_terminal_count": len(connections),
            }
        )

    known_connections = {
        (
            str(connection["instance"]).casefold(),
            str(connection["terminal"]).casefold(),
            str(connection["net"]).casefold(),
        )
        for connection in all_connections
        if connection["instance"] is not None and connection["net"] is not None
    }
    for net_entry in net_entries:
        for connection in net_entry["connections"]:
            instance_name = str(connection["instance"]).casefold()
            terminal_name = str(connection["terminal"]).casefold()
            net_name = str(connection["net"]).casefold()
            if (instance_name, terminal_name) not in instance_terminal_names:
                raise BlockedInput(
                    f"{path}: net {connection['net']!r} references unknown "
                    f"instance terminal {connection['instance']}.{connection['terminal']}",
                    code="connection_mismatch",
                )
            if (instance_name, terminal_name, net_name) not in known_connections:
                raise BlockedInput(
                    f"{path}: net {connection['net']!r} disagrees with "
                    f"{connection['instance']}.{connection['terminal']}",
                    code="connection_mismatch",
                )

    for connection in all_connections:
        instance_name, terminal_name, net = connection["instance"], connection["terminal"], connection["net"]
        if net is None:
            continue
        if instance_name is None:
            continue
        if (
            instance_name.casefold(),
            terminal_name.casefold(),
            str(net).casefold(),
        ) not in declared_net_connections:
            raise BlockedInput(
                f"{path}: instance terminal {instance_name}.{terminal_name} is absent from net {net!r}",
                code="connection_mismatch",
            )

    top_ports: list[dict[str, Any]] = []
    top_port_names: set[str] = set()
    for terminal in terminals:
        subject = f"{path}: top terminal {terminal.get('name')!r}"
        name = _text(terminal.get("name"), "name", subject, required=True)
        key = name.casefold()
        if key in top_port_names:
            raise BlockedInput(f"{path}: duplicate top terminal {name!r}", code="duplicate_name")
        top_port_names.add(key)
        if "pins" not in terminal:
            raise BlockedInput(
                f"{subject}: pins list is required for top-level connectivity",
                code="missing_terminal_connections",
            )
        pins = _as_dicts(terminal.get("pins"), "pins", subject)
        pin_entries = []
        for pin in pins:
            if "net" not in pin:
                raise BlockedInput(
                    f"{subject}: pin.net is required (null means explicitly unconnected)",
                    code="missing_terminal_connections",
                )
            pin_entries.append({"net": _text(pin.get("net"), "pin.net", subject)})
        symbol = symbol_by_name.get(key, {})
        schematic_direction = _text(terminal.get("direction"), "direction", subject)
        schematic_data_type = _text(terminal.get("data_type"), "data_type", subject)
        schematic_port_kind = _text(terminal.get("port_kind"), "port_kind", subject)
        for field, schematic_value, symbol_value in (
            ("direction", schematic_direction, symbol.get("direction")),
            ("data_type", schematic_data_type, symbol.get("data_type")),
            ("port_kind", schematic_port_kind, symbol.get("port_kind")),
        ):
            if schematic_value is not None and symbol_value is not None:
                if schematic_value.casefold() != str(symbol_value).casefold():
                    raise BlockedInput(
                        f"{path}: terminal {name!r} {field} differs between schematic "
                        f"({schematic_value!r}) and symbol ({symbol_value!r})",
                        code="field_mismatch",
                    )
        top_ports.append(
            {
                "name": name,
                "direction": schematic_direction or symbol.get("direction"),
                "bits": _integer(terminal.get("bits"), "bits", subject),
                "data_type": schematic_data_type or symbol.get("data_type"),
                "port_kind": schematic_port_kind or symbol.get("port_kind"),
                "order": symbol.get("order"),
                "pins": pin_entries,
            }
        )
        for pin in pin_entries:
            if pin["net"] is not None:
                all_connections.append({"instance": None, "terminal": name, "net": pin["net"]})

    # Add top-level pins to the reverse net index after all terminal pins have
    # been normalized.  The MCP payload intentionally keeps this information
    # on the terminal side to avoid duplicating large connection arrays.
    net_by_name = {str(item["name"]).casefold(): item for item in net_entries}
    for connection in all_connections:
        if connection["instance"] is not None or connection["net"] is None:
            continue
        net_entry = net_by_name.get(str(connection["net"]).casefold())
        if net_entry is None:
            continue
        net_entry["connections"].append(connection)
        net_entry["instance_terminal_count"] = len(net_entry["connections"])

    missing_nets = sorted(
        {
            str(connection["net"]).casefold()
            for connection in all_connections
            if connection["net"] is not None
        }
        - net_names
    )
    if missing_nets:
        raise BlockedInput(
            f"{path}: connections refer to undeclared net(s): {', '.join(missing_nets)}",
            code="connection_mismatch",
        )

    return {
        "name": identity["cell"],
        "line": 0,
        "ports": [str(port["name"]) for port in sorted(top_ports, key=lambda item: (item["order"] is None, item["order"] or 0))],
        "port_count": len(top_ports),
        "device_count": 0,
        "device_summary": {},
        "submasters": sorted({str(item["master"]) for item in instance_entries}),
        "internal_nets": sorted(
            str(item["name"])
            for item in net_entries
            if str(item["name"]).casefold()
            not in {str(port["name"]).casefold() for port in top_ports}
        ),
        "instances": instance_entries,
        "terminals": top_ports,
        "connections": all_connections,
        "nets": net_entries,
        "name_map": {},
        "provenance": {
            "structure_source": "mcp.inspect_schematic",
            "interface_source": "mcp.inspect_symbol_ports",
            "identity": identity,
            "ignored_schematic_instances": ignored_instances,
        },
    }
