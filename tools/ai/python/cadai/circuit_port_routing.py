"""External ports keep labelled stubs independently of internal net routing."""

from .circuit_geometry_schema import bounds, transform_box
from .circuit_pin_style import placed_pin_orientation


def stub_port(wire, layout):
    return ("port" in wire["endpoint"]
            and (layout.get("routing") or {}).get("ports", "stub") == "stub")


def port_obstacles(plan, layout, body, wires):
    """Reserve the published builtin pin bounds plus its intentional stub.

    The basic pin bounds come from the bundled standard package (the confirmed
    symbol bbox, equal to the master polygon outline).  Keep these boxes for
    every net, including same-net device routing: an intentional port stub must
    not accidentally become an internal trunk.
    """
    from .pdk_builtin import BuiltinPdk

    ports = {p["name"]: p for p in plan["spec"]["ports"]}
    result = {}
    bounds_by_cell = {}
    for index, wire in enumerate(wires):
        if not stub_port(wire, layout):
            continue
        name = wire["endpoint"]["port"]
        port, position = ports[name], layout["ports"][name]
        cell = {"input": "ipin", "output": "opin", "inputOutput": "iopin"}[port["direction"]]
        if cell not in bounds_by_cell:
            bounds_by_cell[cell] = BuiltinPdk().symbol("basic", cell)["bbox"]
        orientation = placed_pin_orientation(position, body, port["direction"])
        box = transform_box(bounds_by_cell[cell], orientation, position["xy"])
        result[("port", index)] = bounds([*box, *wire["points"]])
    return result


def port_stub_issues(plan, layout, body, wires):
    """Detect accidental contact even between labelled stubs on the same net."""
    from .circuit_wiring import segment_hits_box

    issues = []
    boxes = port_obstacles(plan, layout, body, wires)
    for (_, index), box in boxes.items():
        port = wires[index]["endpoint"]["port"]
        for other_index, wire in enumerate(wires):
            if other_index == index:
                continue
            if any(segment_hits_box(a, b, box, max(layout["clearance"], 1e-8))
                   for a, b in zip(wire["points"], wire["points"][1:])):
                issues.append(dict(code="wire_touches_port_stub_region", port=port,
                                   endpoint=wire["endpoint"]))
    return issues
