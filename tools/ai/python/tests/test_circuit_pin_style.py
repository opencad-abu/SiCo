"""Pin orientation and stub direction are planned as two independent facts.

The oracle for the pin rotation is the bundled ``basic`` master geometry: the
arrow end of ``ipin``/``opin`` must face the circuit for inputs and away from it
for outputs, whatever stub direction the plan draws.
"""

from __future__ import annotations

import pytest
from circuit_geometry_fixture import sample_geometry

from cadai import pdk_builtin
from cadai.circuit_create import payload
from cadai.circuit_geometry import preview_geometry
from cadai.circuit_geometry_schema import transform
from cadai.circuit_pin_style import (
    ARROW_ORIENTATION,
    INWARD_ESCAPE,
    OUTWARD,
    planned_pin_orientation,
    port_pin_orientation,
)
from cadai.circuit_spec_plan import preview_circuit

EPS = 1e-9
DIRECTIONS = ("input", "inputOutput", "output")


def _unwrap(value):
    if isinstance(value, dict):
        if "status" in value and "value" in value:
            return _unwrap(value["value"])
        return {key: _unwrap(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_unwrap(item) for item in value]
    return value


# The retired capture snapshot carried the raw master polygons; the bundled
# standard package keeps only the confirmed bbox/anchors.  The reviewed shapes
# are recorded here (the same values the P07/P08 evidence read from the saved
# masters), and the shipped bounds are checked against them below.
MASTER_POLYGONS = {
    "ipin": [[-0.125, -0.05625], [-0.125, 0.05625], [-0.05625, 0.05625], [0.0, 0.0],
             [-0.05625, -0.05625]],
    "opin": [[0.0, -0.05625], [0.0, 0.05625], [0.06875, 0.05625], [0.125, 0.0],
             [0.06875, -0.05625]],
    "iopin": [[0.0875, 0.0], [0.03125, -0.05625], [-0.03125, -0.05625], [-0.0875, 0.0],
              [-0.03125, 0.05625], [0.03125, 0.05625]],
}


def _master_polygon(cell):
    return MASTER_POLYGONS[cell]


def test_recorded_polygons_match_the_shipped_bounds():
    builtin = pdk_builtin.BuiltinPdk()
    for cell, polygon in MASTER_POLYGONS.items():
        xs = [point[0] for point in polygon]
        ys = [point[1] for point in polygon]
        assert builtin.symbol("basic", cell)["bbox"] == [[min(xs), min(ys)], [max(xs), max(ys)]]


def _along(points, vector):
    return [point[0] * vector[0] + point[1] * vector[1] for point in points]


def test_every_direction_and_edge_maps_to_a_rotation_only():
    expected = {
        ("input", "left"): "R0",
        ("input", "right"): "R180",
        ("input", "top"): "R270",
        ("input", "bottom"): "R90",
        ("inputOutput", "left"): "R0",
        ("inputOutput", "right"): "R180",
        ("inputOutput", "top"): "R270",
        ("inputOutput", "bottom"): "R90",
        ("output", "right"): "R0",
        ("output", "left"): "R180",
        ("output", "top"): "R90",
        ("output", "bottom"): "R270",
    }
    for key, orientation in expected.items():
        assert port_pin_orientation(*key) == orientation
    # Mirrors would flip the pin name text; only rotations are planned.
    assert sorted(ARROW_ORIENTATION.values()) == ["R0", "R180", "R270", "R90"]
    for direction in DIRECTIONS:
        for side in OUTWARD:
            assert port_pin_orientation(direction, side) in ARROW_ORIENTATION.values()


@pytest.mark.parametrize("cell,name", [("ipin", "input"), ("opin", "output"), ("iopin", "inputOutput")])
def test_master_geometry_confirms_the_arrow_semantics(cell, name):
    """The real pin polygons must point into the circuit (inputs) or away (outputs)."""
    polygon = _master_polygon(cell)
    for direction in DIRECTIONS:
        master = "ipin" if direction == "input" else "opin" if direction == "output" else "iopin"
        if master != cell:
            continue
        for side, outward in OUTWARD.items():
            orientation = port_pin_orientation(direction, side)
            placed = [transform(point, orientation, (0, 0)) for point in polygon]
            along = _along(placed, outward)
            inner, outer = min(along), max(along)
            at_inner = [value for value in along if abs(value - inner) <= EPS]
            at_outer = [value for value in along if abs(value - outer) <= EPS]
            if master == "iopin":
                # A bidirectional pin straddles its origin on the axis it faces.
                assert abs(inner + outer) <= EPS, (direction, side, along)
                assert len(at_inner) == 1 and len(at_outer) == 1, (direction, side, along)
                continue
            pointed_inner = len(at_inner) == 1
            pointed_outer = len(at_outer) == 1
            # The connection origin is the innermost point of the shape.
            assert abs(inner) <= EPS, (direction, side, along)
            if direction == "output":
                assert pointed_outer and not pointed_inner, (direction, side, along)
            else:
                assert pointed_inner and not pointed_outer, (direction, side, along)


def _ports(args, rows):
    args["spec"]["ports"] = [
        {"name": name, "net": name, "direction": direction} for name, direction, _, _ in rows
    ]
    args["layout"]["ports"] = {
        name: {"xy": xy, "escape": escape} for name, _, xy, escape in rows
    }
    args["preview_digest"] = preview_circuit(args["spec"], args["bindings"])["preview_digest"]


def _port_facts(rows, routing=None):
    args = sample_geometry()
    _ports(args, rows)
    if routing:
        args["layout"]["routing"] = routing
    plan = preview_geometry(**args)["plan"]
    wires = {
        wire["endpoint"]["port"]: wire["points"]
        for wire in plan["wires"]
        if "port" in wire["endpoint"]
    }
    return {port["name"]: port for port in plan["ports"]}, wires, plan


def test_stub_direction_never_rotates_the_pin():
    """The escape only paints the wire; the pin rotation is planned separately."""
    facts, wires, plan = _port_facts(
        [("IN", "input", [-6, -1], "right"), ("OUT", "output", [6, 0], "left")],
        {"mode": "stub"},
    )
    assert plan["geometry_checks_passed"], plan["issues"]
    assert facts["IN"]["orientation"] == "R0" and facts["OUT"]["orientation"] == "R0"
    assert wires["IN"] == [[-6, -1], [-5.75, -1]]
    assert wires["OUT"] == [[6, 0], [5.75, 0]]
    # A stub that points away from the circuit draws the note outside and is rejected.
    outward, outward_wires, outward_plan = _port_facts(
        [("IN", "input", [-6, -1], "left"), ("OUT", "output", [6, 0], "right")],
        {"mode": "stub"},
    )
    assert outward["IN"]["orientation"] == "R0" and outward["OUT"]["orientation"] == "R0"
    assert outward_wires["IN"] == [[-6, -1], [-6.25, -1]]
    assert outward_wires["OUT"] == [[6, 0], [6.25, 0]]
    # Outward is legal but reviewed: the note lands outside the block.
    assert outward_plan["geometry_checks_passed"], outward_plan["issues"]
    assert {row["code"] for row in outward_plan["layout_review"]} == {"port_stub_points_outward"}
    assert {row["preferred"] for row in outward_plan["layout_review"]} == {"right", "left"}


def test_orientation_follows_the_edge_the_port_faces():
    right_input, _, _ = _port_facts([("IN", "input", [6, 1], "left")])
    assert right_input["IN"]["orientation"] == "R180"
    left_output, _, _ = _port_facts([("OUT", "output", [-6, 1], "right")])
    assert left_output["OUT"]["orientation"] == "R180"
    # The horizontal band wins: a left-band port below the body corner stays left.
    assert planned_pin_orientation([-6, -20], [[-1, -1], [1, 1]], "input") == "R0"
    assert planned_pin_orientation([6, -20], [[-1, -1], [1, 1]], "output") == "R0"
    # Ports inside the body footprint fall back to the direction-implied edge.
    assert planned_pin_orientation([0, 0], [[-1, -1], [1, 1]], "input") == "R0"
    assert INWARD_ESCAPE == {"left": "right", "right": "left", "top": "down", "bottom": "up"}


def test_creation_payload_carries_both_facts_per_port():
    args = sample_geometry()
    _ports(
        args,
        [
            ("IN", "input", [-6, -1], "right"),
            ("OUT", "output", [6, 0], "left"),
            ("gnd!", "inputOutput", [-6, 1], "right"),
        ],
    )
    args["geometry_plan_digest"] = preview_geometry(**args)["geometry_plan_digest"]
    _, _, ports, _, _ = payload(args)
    assert ports == [
        ["IN", "input", [-6, -1], "right", "R0"],
        ["OUT", "output", [6, 0], "left", "R0"],
        ["gnd!", "inputOutput", [-6, 1], "right", "R0"],
    ]
