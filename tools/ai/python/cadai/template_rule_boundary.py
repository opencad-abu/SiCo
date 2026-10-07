"""Validate a single residual group connected through declared core boundaries."""

from .template_schema import TemplateError


def boundary_group(contract, source_pins, target, device_map, net_map):
    devices = {d["id"]: d for d in target["devices"]}
    residual = set(devices) - set(device_map.values())
    # ``device_map`` covers every retained source device.  That includes an
    # optional group when the caller keeps it.  The protected core is only a
    # subset of that mapping; requiring equality here incorrectly rejected a
    # perfectly valid target which retained an optional peripheral.  The
    # embedding verifier already checks exact source coverage, so this check
    # only establishes that every protected source device was mapped.
    core = {device_map[d] for d in contract["core_devices"]}
    if not set(contract["core_devices"]).issubset(device_map):
        raise TemplateError("boundary mapping does not cover the complete protected core")
    boundaries = {source_pins[(b["endpoint"]["device"], b["endpoint"]["terminal"])]: b
                  for b in contract["boundary_terminals"]}
    target_boundaries = {net_map[n]: b for n, b in boundaries.items()}
    mapped_nets = set(net_map.values())
    links, reached = [], set()
    for net, boundary in target_boundaries.items():
        attached = [
            (d["id"], p["name"])
            for d in target["devices"]
            if d["id"] not in core
            for p in d["pins"]
            if p["net"] == net
        ]
        if not (boundary["min_additional_connections"] <= len(attached)
                <= boundary["max_additional_connections"]):
            raise TemplateError("boundary_connection_count: target exceeds declared range")
        if any(devices[d]["kind"] not in boundary["allowed_device_kinds"] for d, _ in attached):
            raise TemplateError("boundary_device_kind: target has a disallowed peripheral device")
        for device, terminal in attached:
            if device in residual:
                links.append(
                    {
                        "source_endpoint": boundary["endpoint"],
                        "target_endpoint": {"instance": device, "terminal": terminal},
                    }
                )
                reached.add(device)
    residual_nets = {}
    for device in sorted(residual):
        for pin in devices[device]["pins"]:
            net = pin["net"]
            if net in mapped_nets and net not in target_boundaries:
                raise TemplateError("closed_boundary: peripheral connection changes protected net")
            residual_nets.setdefault(net, set()).add(device)
    if residual:
        # A single group must be connected through its own wires, not only through
        # the core. Ports can terminate the group, but cannot bridge its devices.
        connected = {min(residual)}
        for _ in residual:
            expanded = connected | set().union(
                *(v for v in residual_nets.values() if v & connected)
            )
            if expanded == connected:
                break
            connected = expanded
        if connected != residual or not reached:
            raise TemplateError("needs_adaptation: residual must be one connected boundary group")
    return sorted(residual), links
