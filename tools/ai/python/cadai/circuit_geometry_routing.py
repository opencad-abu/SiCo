"""Internal net routing adapters; external ports default to independent stubs."""

from .circuit_geometry_schema import bounds
from .circuit_port_routing import port_obstacles, stub_port
from .circuit_spec_schema import canonical
from .circuit_wiring import route_nets, routing_metrics
from .template_pg import GROUND_STEMS, POWER_STEMS, _stem


def _pg_nets(plan, layout):
    """Ground-scope nets plus the supply nets that keep labelled stubs.

    Power/ground pins keep labelled stubs by default: PG rails are conventionally
    drawn as short labelled stubs, and narrow channels stay clean instead of
    detouring across the sheet. Pass routing.pg="route" to route them end to end.
    """
    spec = plan["spec"]
    routing = layout.get("routing") or {}
    ground_nets = {net["name"] for net in spec["nets"] if net["scope"] == "ground"}
    pg_nets = set()
    if routing.get("pg", "stub") != "route":
        for port in spec["ports"]:
            role = (layout["ports"].get(port["name"]) or {}).get("role")
            if role in {"power", "ground"}:
                pg_nets.add(port["net"])
        for net in spec["nets"]:
            if _stem(net["name"]) in POWER_STEMS | GROUND_STEMS:
                pg_nets.add(net["name"])
    return ground_nets, pg_nets


def _terminal_order(wire):
    anchor = wire["points"][0]
    return (anchor[0], anchor[1], canonical(wire["endpoint"]))


def _route_cadence(plan, wires, layout):
    """Publish connection edges for the native Cadence router.

    The native side owns the coordinates: once the instances and pins exist it
    reads every ``instTerm`` pin-figure centre (transformed by the instance
    transform, pin instances included) and lets ``schCreateWire(cv "route" ...)``
    lay the path. A preview therefore returns deterministic edges - one per
    consecutive terminal pair of a net's own spanning tree - plus each endpoint's
    escape direction and the planned stub length used when a route fails.
    Reported metrics are planned values; creation reports the router's own result.
    """
    routing = layout.get("routing") or {}
    ground_nets, pg_nets = _pg_nets(plan, layout)
    excluded = ground_nets | pg_nets
    kept, by_net = [], {}
    for wire in wires:
        if wire.get("end_endpoint") or wire["net"] in excluded or stub_port(wire, layout):
            kept.append(wire)
        else:
            by_net.setdefault(wire["net"], []).append(wire)
    edges, singles, planned = [], [], 0
    for net in sorted(by_net):
        members = sorted(by_net[net], key=_terminal_order)
        if len(members) < 2:
            singles.extend(members)
            continue
        planned += len(members)
        for index, (first, second) in enumerate(zip(members, members[1:])):
            edges.append(
                {
                    "endpoint": first["endpoint"],
                    "end_endpoint": second["endpoint"],
                    "net": net,
                    "points": [],
                    "kind": "cadence_route",
                    "label": {"text": net, "xy": None} if index == 0 else None,
                    "refs": [first["endpoint"], second["endpoint"]],
                    "escapes": [first["escape"], second["escape"]],
                    "stub_length": layout["stub_length"],
                }
            )
    wires = kept + singles + edges
    pg_stub_terminals = sum(1 for wire in wires if wire["net"] in pg_nets and wire.get("label"))
    return wires, {
        "mode": "end_to_end",
        "engine": "cadence_route",
        "pg_style": routing.get("pg", "stub"),
        "pg_stub_terminals": pg_stub_terminals,
        "port_style": routing.get("ports", "stub"),
        "port_stub_terminals": sum(stub_port(w, layout) for w in wires),
        "edges": len(edges),
        "fallbacks": [],
        "junctions": [],
        "routed_terminals": planned,
        "metrics": {
            "terminals": planned,
            "routed_terminals": planned,
            "fallback_wires": 0,
            "end_to_end_ratio": 1.0,
            "labelled_wires": sum(1 for wire in wires if wire.get("label")),
            "bends": 0,
            "junctions": 0,
        },
    }


def _route_end_to_end(plan, placed, pins, wires, layout):
    """Replace per-pin stubs with orthogonal end-to-end routes where possible."""
    ground_nets, pg_nets = _pg_nets(plan, layout)
    routing = layout.get("routing") or {}
    terminals = []
    for wire in wires:
        if (wire.get("end_endpoint") or wire["net"] in ground_nets | pg_nets
                or stub_port(wire, layout)):
            continue
        points = wire["points"]
        start, end = points[0], points[-1]
        delta = [end[0] - start[0], end[1] - start[1]]
        length = max(abs(delta[0]), abs(delta[1]))
        if length <= 0:
            continue  # degenerate wire: keep it as drawn
        terminals.append(
            {
                "endpoint": wire["endpoint"],
                "net": wire["net"],
                "anchor": start,
                "escape": (delta[0] / length if length else 0, delta[1] / length if length else 0),
                "owner": wire["endpoint"].get("instance"),
            }
        )
    body = bounds([p for row in placed.values() for p in row["bbox"]])
    routed = route_nets(
        terminals,
        boxes={**{key: row["bbox"] for key, row in placed.items()},
               **port_obstacles(plan, layout, body, wires)},
        grid=layout["grid"],
        clearance=layout["clearance"],
        max_bends=(layout.get("routing") or {}).get("max_bends", 3),
        max_detour=routing.get("max_detour", 2.5),
        stub_length=layout["stub_length"],
        pins=[{"xy": pin["xy"], "net": pin["net"]} for pin in pins],
        # Every planned stub is a keep-out for other nets: a net that is routed
        # later replaces its own stubs, while peers keep routing around them.
        reserved=wires,
    )
    failed = {canonical(fallback["endpoint"]) for fallback in routed["fallbacks"]}
    kept = [
        wire
        for wire in wires
        if wire.get("end_endpoint")
        or wire["net"] in ground_nets | pg_nets
        or stub_port(wire, layout)
        or canonical(wire["endpoint"]) in failed
    ]
    wires = kept + routed["wires"]
    pg_stub_terminals = sum(
        1 for wire in wires if wire["net"] in pg_nets and wire.get("label")
    )
    return wires, {
        "mode": "end_to_end",
        "engine": "planner",
        "pg_style": routing.get("pg", "stub"),
        "pg_stub_terminals": pg_stub_terminals,
        "port_style": routing.get("ports", "stub"),
        "port_stub_terminals": sum(stub_port(w, layout) for w in wires),
        "fallbacks": routed["fallbacks"],
        "junctions": routed["junctions"],
        "routed_terminals": routed["routed_terminals"],
        "metrics": routing_metrics(routed["routed_terminals"], routed["fallbacks"], wires),
    }
