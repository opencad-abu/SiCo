"""Loss-aware translation and exact endpoint revalidation of explicit template maps."""

from __future__ import annotations

import re

from .circuit_spec_schema import SPEC_VERSION, CircuitSpecError, canonical, unique
from .template_circuit_nets import mapped_nets
from .template_schema import TemplateUnavailable, require_legacy_consumer


def source_parts(record):
    require_legacy_consumer(record)
    top = record.get("topology")
    if not top or top.get("level") != "direct":
        raise TemplateUnavailable("template circuit preview requires a direct schematic topology")
    if not 1 <= len(top["devices"]) <= 64 or len(top["nets"]) > 256 or len(top["ports"]) > 128:
        raise TemplateUnavailable("template exceeds 64 devices / 256 nets / 128 ports")
    devices = unique(top["devices"], "id", "template.devices")
    nets = unique(top["nets"], "id", "template.nets")
    ports = unique(top["ports"], "name", "template.ports")
    used = set()
    names = list(ports)
    for device in devices.values():
        pins = unique(device["pins"], "name", "template.device.pins")
        if not pins or len(pins) > 64:
            raise TemplateUnavailable("template device requires 1..64 saved terminals")
        used.update(p["net"] for p in pins.values() if p["net"] is not None)
        names.extend([device["source_name"], *pins])
    for port in ports.values():
        if port["net"] is None:
            raise TemplateUnavailable("template port has no saved net: " + port["name"])
        used.add(port["net"])
        if port["num_bits"] != 1:
            raise TemplateUnavailable("generic circuit spec v1 does not support bus bundles")
    if used - set(nets):
        raise TemplateUnavailable("template endpoint references an absent net")
    for key in sorted(used):
        net = nets[key]
        names.append(net["source_name"])
        if net["num_bits"] != 1:
            raise TemplateUnavailable("generic circuit spec v1 does not support bus bundles")
        if type(net["is_global"]) is not bool:
            raise TemplateUnavailable("template global semantics are unknown")
        if net.get("sig_type") not in {"signal", "supply", "ground"}:
            raise TemplateUnavailable(
                "unknown net sigType cannot be mapped: " + str(net.get("sig_type"))
            )
    for name in names:
        if any(c in name for c in "<>[]"):
            if not re.fullmatch(r"[^<>\[\]]+<[0-9]+>!?", name):
                raise TemplateUnavailable("bus bundles/instance arrays require explicit expansion")
            if top.get("net_global_semantics") != "uniform_boolean_v1":
                raise TemplateUnavailable(
                    "recapture bus member template with templates v4 global semantics"
                )
    return devices, {k: nets[k] for k in sorted(used)}, ports


def exact_keys(actual, expected, label):
    if set(actual) != set(expected):
        raise CircuitSpecError(
            label
            + " must cover exactly the source objects; missing="
            + str(sorted(set(expected) - set(actual)))
            + ", extra="
            + str(sorted(set(actual) - set(expected)))
        )


def unique_values(values, label):
    if len(values) != len(set(values)):
        raise CircuitSpecError(label + " must be one-to-one; merging is not supported")


def resolve_maps(record, selections, net_map, port_map, net_scope_map=None):
    devices, nets, ports = source_parts(record)
    selected = unique(selections, "source_device", "devices")
    exact_keys(selected, devices, "device selections")
    unique_values([s["instance"] for s in selections], "device selections")
    if set(net_map) - set(nets) or set(port_map) - set(ports):
        raise CircuitSpecError("net_map/port_map contains absent or endpoint-free source objects")
    nets_out = {k: net_map.get(k, n["source_name"]) for k, n in nets.items()}
    ports_out = {k: port_map.get(k, k) for k in ports}
    unique_values(list(nets_out.values()), "net_map")
    unique_values(list(ports_out.values()), "port_map")
    mapped_nets(nets, ports, nets_out, net_scope_map or {})
    for key, port in ports.items():
        if ports_out[key] != nets_out[port["net"]]:
            raise CircuitSpecError(
                "generic spec v1 requires port name == net name; explicitly map port "
                + key
                + " to "
                + nets_out[port["net"]]
                + " or use a compatible template"
            )
    device_maps = []
    for key, device in sorted(devices.items()):
        selection = selected[key]
        pins = {p["name"] for p in device["pins"]}
        renames = selection.get("terminal_map", {})
        if set(renames) - pins:
            raise CircuitSpecError("terminal_map contains absent source terminals: " + key)
        terminal_map = {p: renames.get(p, p) for p in sorted(pins)}
        unique_values(list(terminal_map.values()), "terminal_map " + key)
        device_maps.append(
            {
                "source_device": key,
                "instance": selection["instance"],
                "master": selection["master"],
                "terminal_map": terminal_map,
            }
        )
    return device_maps, nets_out, ports_out


def build_spec(record, args):
    from .template_circuit_schema import USE_VERSION

    devices, nets, ports = source_parts(record)
    maps, net_map, port_map = resolve_maps(
        record,
        args["devices"],
        args.get("net_map", {}),
        args.get("port_map", {}),
        args.get("net_scope_map", {}),
    )
    selections = {s["source_device"]: s for s in args["devices"]}
    instances = []
    for m in maps:
        device, selection = devices[m["source_device"]], selections[m["source_device"]]
        open_pins = {p["name"] for p in device["pins"] if p["net"] is None}
        reasons = selection.get("unconnected", {})
        exact_keys(reasons, open_pins, "explicit unconnected reasons " + device["id"])
        instance = {
            "id": m["instance"],
            "master": m["master"],
            "role": "device",
            "parameters": selection["parameters"],
            "connections": {
                m["terminal_map"][p["name"]]: net_map[p["net"]]
                for p in device["pins"]
                if p["net"] is not None
            },
            "unconnected": {m["terminal_map"][p]: reason for p, reason in reasons.items()},
        }
        if "name" in selection:
            instance["name"] = selection["name"]
        instances.append(instance)
    spec = {
        "schema": SPEC_VERSION,
        "project_ref": args["bindings"]["project_ref"],
        "binding_snapshot": args["bindings"]["snapshot_ref"],
        "kind": "circuit",
        "target": args["target"],
        "instances": instances,
        "nets": mapped_nets(nets, ports, net_map, args.get("net_scope_map", {})),
        "ports": [
            {"name": port_map[k], "net": net_map[p["net"]], "direction": p["direction"]}
            for k, p in sorted(ports.items())
        ],
    }
    use = {
        "schema": USE_VERSION,
        "template_ref": record["template_ref"],
        "devices": maps,
        "net_map": net_map,
        "port_map": port_map,
    }
    if args.get("net_scope_map"):
        use["net_scope_map"] = dict(args["net_scope_map"])
    return spec, use


def verify_use(record, spec, use, bindings):
    """Compare every endpoint. No graph symmetry or terminal permutation is inferred."""
    if spec["kind"] != "circuit":
        raise CircuitSpecError(
            "template-use v1 supports whole circuit specs; TB layout remains role-based"
        )
    devices, nets, ports = source_parts(record)
    maps, net_map, port_map = resolve_maps(
        record, use["devices"], use["net_map"], use["port_map"], use.get("net_scope_map", {})
    )
    exact_keys(use["net_map"], nets, "template_use.net_map")
    exact_keys(use["port_map"], ports, "template_use.port_map")
    for m in maps:
        original = next(s for s in use["devices"] if s["source_device"] == m["source_device"])
        exact_keys(original["terminal_map"], m["terminal_map"], "template_use.terminal_map")
    target_instances = unique(spec["instances"], "id", "spec.instances")
    exact_keys(target_instances, [m["instance"] for m in maps], "template_use target instances")
    expected_nets = mapped_nets(nets, ports, net_map, use.get("net_scope_map", {}))
    expected_ports = [
        {"name": port_map[k], "direction": p["direction"], "net": net_map[p["net"]]}
        for k, p in ports.items()
    ]
    if sorted(spec["nets"], key=canonical) != sorted(expected_nets, key=canonical):
        raise CircuitSpecError("spec nets differ from the explicit template mapping")
    if sorted(spec["ports"], key=canonical) != sorted(expected_ports, key=canonical):
        raise CircuitSpecError("spec ports/directions differ from the explicit template mapping")
    masters = {m["id"]: m for m in bindings["masters"]}
    for m in maps:
        source, target = devices[m["source_device"]], target_instances[m["instance"]]
        if target["master"] != m["master"]:
            raise CircuitSpecError("target master selection changed without updating template_use")
        master = masters.get(m["master"])
        role = {"device": "device", "hierarchy": "design"}.get(source["role"])
        if master and role and master["kind"] != role:
            raise CircuitSpecError("source device/design role conflicts with selected master kind")
        expected = {
            m["terminal_map"][p["name"]]: net_map[p["net"]]
            for p in source["pins"]
            if p["net"] is not None
        }
        opens = {m["terminal_map"][p["name"]] for p in source["pins"] if p["net"] is None}
        if target["connections"] != expected or set(target["unconnected"]) != opens:
            raise CircuitSpecError(
                "target endpoints differ from explicit template mapping: " + m["instance"]
            )
    return {**use, "devices": maps, "net_map": net_map, "port_map": port_map}
