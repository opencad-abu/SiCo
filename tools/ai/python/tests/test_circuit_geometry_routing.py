"""End-to-end routing mode: integration through preview_circuit_geometry."""

from __future__ import annotations

import pytest
from circuit_geometry_fixture import sample_geometry

from cadai.circuit_create import payload
from cadai.circuit_geometry import preview_geometry
from cadai.circuit_geometry_schema import LAYOUT
from cadai.circuit_spec_plan import preview_circuit
from cadai.circuit_spec_schema import CircuitSpecError, canonical, digest, validate


def _ports(args, rows):
    args["spec"]["ports"] = [
        {"name": name, "net": name, "direction": direction}
        for name, direction, _, _ in rows
    ]
    args["layout"]["ports"] = {
        name: {"xy": xy, "escape": escape} for name, _, xy, escape in rows
    }
    args["preview_digest"] = preview_circuit(args["spec"], args["bindings"])["preview_digest"]


def routed_sample(engine="planner"):
    args = sample_geometry()
    _ports(
        args,
        [
            ("gnd!", "inputOutput", [-6, 1], "right"),
            ("IN", "input", [-6, -1], "right"),
            ("OUT", "output", [6, 0], "left"),
        ],
    )
    args["layout"]["routing"] = {
        "mode": "end_to_end", "engine": engine, "max_bends": 4, "ports": "route"}
    return args


def _wires_of(plan, net):
    return [wire for wire in plan["wires"] if wire["net"] == net]


def test_end_to_end_mode_joins_pins_and_keeps_one_label_per_net():
    plan = preview_geometry(**routed_sample())["plan"]
    assert plan["geometry_checks_passed"], plan["issues"]
    assert plan["routing"]["mode"] == "end_to_end"
    assert plan["routing"]["fallbacks"] == []
    for net in ("IN", "OUT"):
        wires = _wires_of(plan, net)
        labels = [wire for wire in wires if wire.get("label")]
        assert len(labels) == 1, (net, wires)
        points = [point for wire in wires for point in wire["points"]]
        assert any(point[1] > 0.5 for point in points)  # routed above the device row
    in_points = {tuple(point) for wire in _wires_of(plan, "IN") for point in wire["points"]}
    assert (-4.0, 0.5) in in_points and (-0.5, 0.0) in in_points
    assert all(len(wire["points"]) >= 2 for wire in plan["wires"])


def test_routing_is_a_user_decision_and_stub_is_the_default():
    """Nothing routing-related is drawn by default; end_to_end must be asked for."""
    args = sample_geometry()
    _ports(
        args,
        [
            ("gnd!", "inputOutput", [-6, 1], "right"),
            ("IN", "input", [-6, -1], "right"),
            ("OUT", "output", [6, 0], "left"),
        ],
    )
    del args["layout"]["routing"]
    plan = preview_geometry(**args)["plan"]
    assert plan["geometry_checks_passed"], plan["issues"]
    assert plan["routing"]["mode"] == "stub"
    assert all(len(wire["points"]) == 2 for wire in plan["wires"])
    assert not [wire for wire in plan["wires"] if wire.get("kind") == "cadence_route"]

    args["layout"]["routing"] = {
        "mode": "end_to_end",
        "decision": {"source": "user", "chosen": "end_to_end"},
    }
    plan = preview_geometry(**args)["plan"]
    assert plan["routing"]["mode"] == "end_to_end"
    assert plan["routing"]["engine"] == "cadence_route"
    assert plan["routing"]["decision"] == {"source": "user", "chosen": "end_to_end"}
    assert [wire for wire in plan["wires"] if wire.get("kind") == "cadence_route"]


def test_power_and_ground_nets_keep_labelled_stubs_by_default():
    args = routed_sample()
    plan = preview_geometry(**args)["plan"]
    assert plan["geometry_checks_passed"], plan["issues"]
    assert plan["routing"]["pg_style"] == "stub"
    assert plan["routing"]["pg_stub_terminals"] >= 1
    ground_wires = _wires_of(plan, "gnd!")
    assert ground_wires and all(len(wire["points"]) == 2 for wire in ground_wires)
    assert all(wire.get("label") for wire in ground_wires)

    routed_pg = routed_sample()
    routed_pg["layout"]["routing"] = {
        "mode": "end_to_end",
        "engine": "planner",
        "max_bends": 4,
        "pg": "route",
    }
    plan_pg = preview_geometry(**routed_pg)["plan"]
    assert plan_pg["geometry_checks_passed"], plan_pg["issues"]
    assert plan_pg["routing"]["pg_style"] == "route"
    assert any(len(wire["points"]) > 2 for wire in _wires_of(plan_pg, "gnd!"))


def test_detour_budget_prefers_a_labelled_stub_over_a_long_route():
    args = routed_sample()
    args["layout"]["routing"] = {
        "mode": "end_to_end", "engine": "planner", "max_bends": 4, "max_detour": 1.05,
    }
    args["spec"]["instances"] = args["spec"]["instances"] + [
        {
            "id": "wall",
            "name": "W1",
            "master": "selected_resistor",
            "role": "auxiliary",
            "parameters": {"value": "1k"},
            "connections": {"P": "gnd!", "N": "gnd!"},
            "unconnected": {},
        }
    ]
    args["layout"]["instances"]["wall"] = {"xy": [-2, 0], "orientation": "R0"}
    args["geometry"]["instances"].append(
        {
            "instance": "wall",
            "master_revision": "rev1",
            "parameters_digest": digest({"value": "1k"}),
            "occupied_bbox": [[-0.5, -0.5], [0.5, 2.5]],
            "terminals": [
                {"name": "P", "anchors": [{"id": "pin0", "xy": [-0.5, 0], "escape": "left"}]},
                {"name": "N", "anchors": [{"id": "pin0", "xy": [0.5, 0], "escape": "right"}]},
            ],
        }
    )
    args["preview_digest"] = preview_circuit(args["spec"], args["bindings"])["preview_digest"]
    plan = preview_geometry(**args)["plan"]
    assert plan["geometry_checks_passed"], plan["issues"]
    assert plan["routing"]["fallbacks"], "a detour beyond the budget must fall back"
    failed = {canonical(item["endpoint"]) for item in plan["routing"]["fallbacks"]}
    assert all(
        len(wire["points"]) == 2 and wire.get("label")
        for wire in plan["wires"]
        if canonical(wire["endpoint"]) in failed
    )


def test_blocked_net_falls_back_to_a_labelled_stub_with_a_reason():
    args = routed_sample()
    args["layout"]["routing"] = {"mode": "end_to_end", "engine": "planner", "max_bends": 1}
    args["spec"]["instances"] = args["spec"]["instances"] + [
        {
            "id": "wall",
            "name": "W1",
            "master": "selected_resistor",
            "role": "auxiliary",
            "parameters": {"value": "1k"},
            "connections": {"P": "gnd!", "N": "gnd!"},
            "unconnected": {},
        }
    ]
    args["layout"]["instances"]["wall"] = {"xy": [-2, 0], "orientation": "R0"}
    args["geometry"]["instances"].append(
        {
            "instance": "wall",
            "master_revision": "rev1",
            "parameters_digest": digest({"value": "1k"}),
            "occupied_bbox": [[-0.5, -0.5], [0.5, 2.5]],
            "terminals": [
                {"name": "P", "anchors": [{"id": "pin0", "xy": [-0.5, 0], "escape": "left"}]},
                {"name": "N", "anchors": [{"id": "pin0", "xy": [0.5, 0], "escape": "right"}]},
            ],
        }
    )
    args["preview_digest"] = preview_circuit(args["spec"], args["bindings"])["preview_digest"]
    plan = preview_geometry(**args)["plan"]
    assert plan["geometry_checks_passed"], plan["issues"]
    fallbacks = plan["routing"]["fallbacks"]
    assert fallbacks, plan["routing"]
    assert all(item["reason"] in {"no_clear_path", "off_grid_terminal"} for item in fallbacks)
    failed = {canonical(item["endpoint"]) for item in fallbacks}
    stubs = [
        wire
        for wire in plan["wires"]
        if canonical(wire["endpoint"]) in failed and wire.get("label")
    ]
    assert len(stubs) == len(failed)
    assert all(len(stub["points"]) == 2 for stub in stubs)


def test_routing_policy_schema_rejects_unknown_modes():
    layout = {
        "instances": {"dut": {"xy": [0, 0], "orientation": "R0"}},
        "ports": {},
        "anchor_choices": {},
        "grid": 0.0625,
        "stub_length": 0.25,
        "clearance": 0.125,
        "routing": {"mode": "auto"},
    }
    with pytest.raises(CircuitSpecError):
        validate(layout, LAYOUT, "layout")
    layout["routing"] = {"mode": "end_to_end", "max_bends": 9}
    with pytest.raises(CircuitSpecError):
        validate(layout, LAYOUT, "layout")
    layout["routing"] = {"mode": "end_to_end", "max_bends": 3}
    validate(layout, LAYOUT, "layout")


def test_interior_pin_anchor_is_allowed_only_when_routing():
    """A PDK bulk pin drawn inside the body must not block a routed plan."""
    routed = routed_sample()
    for row in routed["geometry"]["instances"]:
        for terminal in row["terminals"]:
            for anchor in terminal["anchors"]:
                anchor["xy"] = [0.0, 0.0]  # centre pin: escape ray stays inside
                anchor["escape"] = "up"
    plan = preview_geometry(**routed)["plan"]
    escapes = plan["routing"].get("interior_escapes")
    assert escapes and all(item["anchor"] for item in escapes)

    stub = {**routed, "layout": {**routed["layout"], "routing": {"mode": "stub"}}}
    with pytest.raises(CircuitSpecError) as excinfo:
        preview_geometry(**stub)
    assert "stub escape must lead out of occupied_bbox" in str(excinfo.value)


def test_creation_payload_keeps_routed_polylines_and_optional_labels():
    args = routed_sample()
    args["geometry_plan_digest"] = preview_geometry(**args)["geometry_plan_digest"]
    native = payload(args)
    target, instances, ports, wires, notes = native
    assert target == ["work_a", "my_tb", "schematic"]
    assert all(len(row) == 3 for row in wires)
    polyline = [row for row in wires if len(row[1]) > 2]
    assert polyline, wires
    labelled = [row for row in wires if row[0]]
    assert labelled, wires
    unlabelled = [row for row in wires if row[0] is None]
    assert unlabelled, "routed nets still need branch wires without labels"


def cadence_sample(pg="stub"):
    args = sample_geometry()
    _ports(
        args,
        [
            ("gnd!", "inputOutput", [-6, 1], "right"),
            ("IN", "input", [-6, -1], "right"),
            ("OUT", "output", [6, 0], "left"),
        ],
    )
    args["layout"]["routing"] = {
        "mode": "end_to_end", "engine": "cadence_route", "pg": pg, "ports": "route"}
    return args


def _edges_of(plan, net=None):
    return [
        wire
        for wire in plan["wires"]
        if wire.get("kind") == "cadence_route" and (net is None or wire["net"] == net)
    ]


def test_cadence_route_is_the_default_engine_and_publishes_connection_edges():
    """Without an explicit engine every multi-terminal net becomes edge rows."""
    args = cadence_sample()
    del args["layout"]["routing"]["engine"]
    plan = preview_geometry(**args)["plan"]
    assert plan["geometry_checks_passed"], plan["issues"]
    assert plan["routing"]["engine"] == "cadence_route"
    assert plan["routing"]["metrics"]["terminals"] == 6
    for net in ("IN", "OUT"):
        edges = _edges_of(plan, net)
        # three terminals per net (dut, stim/load, port): one edge per pair
        assert len(edges) == 2, edges
        assert all(edge["points"] == [] for edge in edges)
        owners = [edge for edge in edges if edge.get("label")]
        assert len(owners) == 1 and owners[0]["label"]["text"] == net
        assert owners[0]["label"]["xy"] is None
    assert _edges_of(plan, "gnd!") == []
    ground_wires = _wires_of(plan, "gnd!")
    assert ground_wires and all(len(wire["points"]) == 2 for wire in ground_wires)


def test_cadence_route_spans_every_routed_net_and_keeps_pg_stubs():
    plan = preview_geometry(**cadence_sample())["plan"]
    assert plan["geometry_checks_passed"], plan["issues"]
    assert plan["routing"]["pg_style"] == "stub"
    assert plan["routing"]["pg_stub_terminals"] >= 1
    terminals = {
        canonical(ref)
        for wire in plan["wires"]
        if wire.get("kind") == "cadence_route"
        for ref in wire["refs"]
    }
    assert terminals == {
        canonical(ref)
        for ref in (
            {"port": "IN"},
            {"instance": "stim", "terminal": "P"},
            {"instance": "dut", "terminal": "IN"},
            {"instance": "dut", "terminal": "OUT"},
            {"instance": "load", "terminal": "P"},
            {"port": "OUT"},
        )
    }
    assert len(terminals) == plan["routing"]["metrics"]["terminals"]
    for net in ("IN", "OUT"):
        owners = [wire for wire in _edges_of(plan, net) if wire.get("label")]
        assert len(owners) == 1, net
    # A three-terminal net keeps one consecutive pair per terminal after the first.
    args = cadence_sample()
    args["spec"]["instances"].append(
        {
            "id": "probe",
            "name": "Rin2",
            "master": "selected_resistor",
            "role": "auxiliary",
            "parameters": {"value": "1k"},
            "connections": {"P": "IN", "N": "gnd!"},
            "unconnected": {},
        }
    )
    args["layout"]["instances"]["probe"] = {"xy": [-2, -2], "orientation": "R0"}
    args["geometry"]["instances"].append(
        {
            "instance": "probe",
            "master_revision": "rev1",
            "parameters_digest": digest({"value": "1k"}),
            "occupied_bbox": [[-0.5, -0.5], [0.5, 0.5]],
            "terminals": [
                {"name": "P", "anchors": [{"id": "pin0", "xy": [0, 0.5], "escape": "up"}]},
                {"name": "N", "anchors": [{"id": "pin0", "xy": [0, -0.5], "escape": "down"}]},
            ],
        }
    )
    args["preview_digest"] = preview_circuit(args["spec"], args["bindings"])["preview_digest"]
    plan = preview_geometry(**args)["plan"]
    assert plan["geometry_checks_passed"], plan["issues"]
    edges = _edges_of(plan, "IN")
    assert len(edges) == 3, edges
    assert sum(1 for wire in edges if wire.get("label")) == 1
    refs = [ref for wire in edges for ref in wire["refs"]]
    assert {canonical(ref) for ref in refs} == {
        canonical({"port": "IN"}),
        canonical({"instance": "stim", "terminal": "P"}),
        canonical({"instance": "dut", "terminal": "IN"}),
        canonical({"instance": "probe", "terminal": "P"}),
    }


def test_cadence_route_ignores_collision_geometry_and_reports_planned_metrics():
    """Planner-only collision checks must not reject an edge handed to the router."""
    args = cadence_sample()
    args["spec"]["instances"].append(
        {
            "id": "wall",
            "name": "W1",
            "master": "selected_resistor",
            "role": "auxiliary",
            "parameters": {"value": "1k"},
            "connections": {"P": "gnd!", "N": "gnd!"},
            "unconnected": {},
        }
    )
    args["layout"]["instances"]["wall"] = {"xy": [-2, 0], "orientation": "R0"}
    args["geometry"]["instances"].append(
        {
            "instance": "wall",
            "master_revision": "rev1",
            "parameters_digest": digest({"value": "1k"}),
            "occupied_bbox": [[-0.5, -0.5], [0.5, 2.5]],
            "terminals": [
                {"name": "P", "anchors": [{"id": "pin0", "xy": [-0.5, 0], "escape": "left"}]},
                {"name": "N", "anchors": [{"id": "pin0", "xy": [0.5, 0], "escape": "right"}]},
            ],
        }
    )
    args["preview_digest"] = preview_circuit(args["spec"], args["bindings"])["preview_digest"]
    plan = preview_geometry(**args)["plan"]
    assert plan["geometry_checks_passed"], plan["issues"]
    assert plan["routing"]["fallbacks"] == []
    assert plan["routing"]["edges"] >= 1
    assert plan["routing"]["metrics"]["fallback_wires"] == 0
    assert plan["routing"]["metrics"]["end_to_end_ratio"] == 1.0


def test_cadence_route_payload_carries_named_endpoints_and_escapes():
    args = cadence_sample()
    args["geometry_plan_digest"] = preview_geometry(**args)["geometry_plan_digest"]
    _, _, ports, wires, _ = payload(args)
    route_rows = [row for row in wires if len(row) == 4 and row[3]]
    assert route_rows, wires
    assert all(row[1] == [] and row[2] is None and row[0] in {"IN", "OUT"} for row in route_rows)
    refs = [ref for row in route_rows for ref in row[3][:2]]
    assert ["port", "IN"] in refs
    # instance ids are translated into the created instance names
    assert ["inst", "Vin", "P"] in refs and ["inst", "Rout", "P"] in refs
    for row in route_rows:
        route = row[3]
        assert route[2] in ([1, 0], [-1, 0], [0, 1], [0, -1])
        assert route[3] in ([1, 0], [-1, 0], [0, 1], [0, -1])
        assert route[4] == args["layout"]["stub_length"]
    owners = [row for row in route_rows if row[3][5]]
    assert len(owners) == len({row[0] for row in route_rows})
    draw_rows = [row for row in wires if len(row) == 3]
    assert draw_rows, wires
    assert {row[0] for row in ports} == {"gnd!", "IN", "OUT"}


def test_port_one_grid_off_the_terminal_row_is_reported():
    """A rounding-scale port offset forces a stray bend; the plan names the row."""
    off = routed_sample()
    off["layout"]["ports"]["OUT"] = {"xy": [6.0, 0.4375], "escape": "left"}
    plan = preview_geometry(**off)["plan"]
    issue = next(row for row in plan["issues"] if row["code"] == "port_off_terminal_row")
    assert issue["port"] == "OUT" and issue["terminal"] == "Rout.P"
    assert issue["y"] == 0.4375 and issue["aligned_y"] == 0.5
    assert not plan["geometry_checks_passed"]
    with pytest.raises(CircuitSpecError):
        payload({**off, "geometry_plan_digest": "0" * 64})

    aligned = routed_sample()
    aligned["layout"]["ports"]["OUT"] = {"xy": [6.0, 0.5], "escape": "left"}
    plan = preview_geometry(**aligned)["plan"]
    assert plan["geometry_checks_passed"], plan["issues"]
    assert [row for row in plan["issues"] if row["code"] == "port_off_terminal_row"] == []


def test_cadence_route_schema_accepts_only_known_engines():
    layout = {
        "instances": {"dut": {"xy": [0, 0], "orientation": "R0"}},
        "ports": {},
        "anchor_choices": {},
        "grid": 0.0625,
        "stub_length": 0.25,
        "clearance": 0.125,
        "routing": {"mode": "end_to_end", "engine": "teflon"},
    }
    with pytest.raises(CircuitSpecError):
        validate(layout, LAYOUT, "layout")
    for engine in ("cadence_route", "planner"):
        layout["routing"] = {"mode": "end_to_end", "engine": engine}
        validate(layout, LAYOUT, "layout")
