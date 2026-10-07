"""Electrical invariants shared by bounded search and explicit P2 adaptation."""

from .template_rule_boundary import boundary_group
from .template_schema import TemplateError
from .template_topology import evidence_gaps, topology_parts


def exact_keys(actual, expected, label):
    if set(actual) != set(expected):
        raise TemplateError(label + ": mapping must cover exactly the retained source objects")


def injective(values, label):
    if len(values) != len(set(values)):
        raise TemplateError(label + ": mapping must be injective")


def verify_embedding(record, source, target, mapping):
    gaps = sorted(set(evidence_gaps(source) + evidence_gaps(target)))
    if gaps:
        raise TemplateError("incomplete_electrical_evidence: " + ", ".join(gaps))
    devices, nets, ports, pins = topology_parts(source)
    targets, target_nets, target_ports, target_pins = topology_parts(target)
    dm, tm, nm, pm = (mapping[k] for k in ("device_map", "terminal_map", "net_map", "port_map"))
    for value, expected, label in ((dm, devices, "devices"), (tm, devices, "terminals"),
                                   (nm, nets, "nets"), (pm, ports, "ports")):
        exact_keys(value, expected, label)
    for value, label in ((dm, "devices"), (nm, "nets"), (pm, "ports")):
        injective(list(value.values()), label)
    if set(dm.values()) - targets.keys() or set(nm.values()) - target_nets.keys():
        raise TemplateError("mapping refers to absent target objects")
    for key, device in devices.items():
        actual = targets[dm[key]]
        if device["kind"] != actual["kind"] or device["role"] != actual["role"]:
            raise TemplateError("device_type_mismatch: selected target type differs")
        if any(actual["attributes"].get(k) != v for k, v in device["attributes"].items()):
            raise TemplateError(
                "device_attributes_mismatch: target classification lacks required facts"
            )
        own = {p["name"] for p in device["pins"]}
        exact_keys(tm[key], own, "device terminals")
        injective(list(tm[key].values()), "device terminals")
        # P2 has no reviewed terminal-alias rule. Explicit maps cannot change
        # electrical pin identity (including D/S and PLUS/MINUS).
        if any(old != new for old, new in tm[key].items()):
            raise TemplateError("terminal_identity_changed: terminal aliases need a reviewed rule")
        if set(tm[key].values()) != {p["name"] for p in actual["pins"]}:
            raise TemplateError("terminal_inventory_mismatch: missing or extra target terminal")
        for pin in device["pins"]:
            wanted = nm[pin["net"]]
            if target_pins[(dm[key], tm[key][pin["name"]])] != wanted:
                raise TemplateError("protected_connection_changed: target endpoint differs")
    for key, net in nets.items():
        actual = target_nets[nm[key]]
        if (net["num_bits"], net["is_global"]) != (
            actual["num_bits"], actual["is_global"]
        ) or (net["is_global"] and net["source_name"] != actual["source_name"]):
            raise TemplateError("net_identity_changed: scope, width or global identity differs")
    for name, port in ports.items():
        actual = target_ports.get(pm[name])
        if actual is None or (port["direction"], port["num_bits"], nm[port["net"]]) != (
            actual["direction"], actual["num_bits"], actual["net"]
        ):
            raise TemplateError("port_identity_changed: direction, width or net differs")
    residual, links = boundary_group(record["reuse_contract"], pins, target, dm, nm)
    extra_ports = sorted(set(target_ports) - set(pm.values()))
    extra_nets = sorted(set(target_nets) - set(nm.values()))
    owned = {p["net"] for d in residual for p in targets[d]["pins"]}
    if set(extra_nets) - owned or any(
        target_ports[p]["net"] not in extra_nets for p in extra_ports
    ):
        raise TemplateError("undeclared_port_or_net: additions must belong to the residual group")
    return {"residual": residual, "links": links, "extra_ports": extra_ports,
            "extra_nets": extra_nets}
