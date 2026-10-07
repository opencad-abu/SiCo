"""Explicit direct-level global-to-port conversion, without inferred connectivity."""

from .circuit_spec_schema import CircuitSpecError


def mapped_nets(nets, ports, names, scopes):
    """Retain legacy identities unless a global has an explicit local boundary port."""
    if set(scopes) - set(nets):
        raise CircuitSpecError("net_scope_map contains absent or endpoint-free source nets")
    boundary = {p["net"] for p in ports.values()}
    result = []
    for key, net in nets.items():
        name = names[key]
        if key in scopes:
            if scopes[key] != "local" or not net["is_global"]:
                raise CircuitSpecError("net_scope_map only supports explicit global-to-local")
            if key not in boundary:
                raise CircuitSpecError("global-to-local requires an existing source boundary port")
            if name.endswith("!") or name == "0":
                raise CircuitSpecError("global-to-local requires a named local net, not ground")
            scope = "local"
        else:
            if net["is_global"] and name != net["source_name"]:
                raise CircuitSpecError(
                    "global net identity must be preserved: " + net["source_name"]
                )
            if name.endswith("!") != net["is_global"]:
                raise CircuitSpecError("net_map changes global scope: " + key)
            scope = "global" if net["is_global"] else "local"
        result.append({"name": name, "scope": scope})
    return result


def scope_evidence(nets, ports, use):
    return [
        {
            "source_net": key,
            "source_name": nets[key]["source_name"],
            "source_scope": "global",
            "target_name": use["net_map"][key],
            "target_scope": "local",
            "boundary_ports": sorted(
                use["port_map"][p["name"]] for p in ports.values() if p["net"] == key
            ),
            "connection_policy": "explicit_boundary_pins; external_global_semantics_not_retained",
        }
        for key in sorted(use.get("net_scope_map", {}))
    ]
