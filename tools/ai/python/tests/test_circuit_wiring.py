"""Deterministic orthogonal end-to-end wiring: unit tests for the planner."""

from __future__ import annotations

from cadai.circuit_spec_schema import canonical
from cadai.circuit_wiring import orthogonal_candidates, route_nets, routing_metrics

GRID = 0.0625


def terminal(name, anchor, escape, net="N1", owner=None):
    return {
        "endpoint": {"instance": name, "terminal": "P"},
        "net": net,
        "anchor": anchor,
        "escape": escape,
        "owner": owner or name,
    }


def wire_points(result):
    return [wire["points"] for wire in result["wires"]]


def test_aligned_terminals_join_with_one_straight_run():
    result = route_nets(
        [
            terminal("A", [0, 0], (1, 0)),
            terminal("B", [1, 0], (-1, 0)),
        ],
        boxes={},
        grid=GRID,
        clearance=GRID,
        stub_length=GRID * 4,
    )
    assert result["fallbacks"] == []
    assert result["junctions"] == []
    assert len(result["wires"]) == 2
    assert wire_points(result)[0] == [[0, 0], [0.25, 0]]
    assert wire_points(result)[1] == [[1, 0], [0.75, 0], [0.25, 0]]
    assert result["wires"][0]["label"] == {"text": "N1", "xy": [0.25, 0]}
    assert result["wires"][1]["label"] is None


def test_offset_terminals_use_an_orthogonal_path():
    result = route_nets(
        [
            terminal("A", [0, 0], (0, 1)),
            terminal("B", [2, 2], (0, -1)),
        ],
        boxes={},
        grid=GRID,
        clearance=GRID,
        stub_length=GRID * 4,
    )
    assert result["fallbacks"] == []
    points = wire_points(result)[-1]
    assert len(points) >= 3
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        assert abs(ax - bx) <= 1e-9 or abs(ay - by) <= 1e-9


def test_obstacle_forces_a_detour_or_reports_a_fallback():
    detour = route_nets(
        [
            terminal("A", [0, 0], (1, 0)),
            terminal("B", [2, 0], (-1, 0)),
        ],
        boxes={"wall": [[0.75, -0.5], [1.25, 0.5]]},
        grid=GRID,
        clearance=GRID,
        stub_length=GRID * 4,
    )
    assert detour["fallbacks"] == []
    last = wire_points(detour)[-1]
    assert len(last) >= 4  # three bends around the wall
    blocked = route_nets(
        [
            terminal("A", [0, 0], (1, 0)),
            terminal("B", [2, 0], (-1, 0)),
        ],
        boxes={
            "wall": [[0.75, -0.5], [1.25, 0.5]],
            "cap1": [[0.75, 0.5], [1.25, 1.5]],
            "cap2": [[0.75, -1.5], [1.25, -0.5]],
        },
        grid=GRID,
        clearance=GRID,
        stub_length=GRID * 4,
        max_bends=2,
    )
    assert blocked["fallbacks"] == [
        {"endpoint": {"instance": "B", "terminal": "P"}, "reason": "no_clear_path"}
    ]
    assert len(blocked["wires"]) == 1  # the first trunk keeps its labelled stub


def test_third_terminal_attaches_to_a_segment_interior_and_marks_a_junction():
    result = route_nets(
        [
            terminal("A", [0, 0], (1, 0)),
            terminal("B", [2, 0], (-1, 0)),
            terminal("C", [1, 1], (0, -1)),
        ],
        boxes={},
        grid=GRID,
        clearance=GRID,
        stub_length=GRID * 4,
    )
    assert result["fallbacks"] == []
    assert len(result["junctions"]) == 1 and result["junctions"][0]["degree"] == 3
    assert len(result["wires"]) == 3
    assert sorted(points[0] for points in wire_points(result)) == [[0, 0], [1, 1], [2, 0]]
    for points in wire_points(result):
        for (ax, ay), (bx, by) in zip(points, points[1:]):
            assert abs(ax - bx) <= 1e-9 or abs(ay - by) <= 1e-9
    metrics = routing_metrics(result["routed_terminals"], result["fallbacks"], result["wires"])
    assert metrics["terminals"] == 3
    assert metrics["routed_terminals"] == 3
    assert metrics["fallback_wires"] == 0
    assert metrics["end_to_end_ratio"] == 1.0
    assert metrics["labelled_wires"] == 1
    assert metrics["junctions"] == 1
    assert metrics["bends"] >= 2  # orthogonal joins always need at least one corner


def test_off_grid_terminal_is_reported_not_rounded():
    result = route_nets(
        [terminal("A", [0.1, 0], (1, 0))],
        boxes={},
        grid=GRID,
        clearance=GRID,
        stub_length=GRID,
    )
    assert result["wires"] == []
    assert result["fallbacks"] == [
        {"endpoint": {"instance": "A", "terminal": "P"}, "reason": "off_grid_terminal"}
    ]


def test_routing_is_deterministic_and_dedupes_repeated_inputs():
    args = (
        [
            terminal("A", [0, 0], (1, 0)),
            terminal("B", [2, 0], (-1, 0)),
            terminal("C", [1, 1], (0, -1)),
        ],
        {},
    )
    first = route_nets(args[0], boxes=args[1], grid=GRID, clearance=GRID, stub_length=0.25)
    second = route_nets(args[0], boxes=args[1], grid=GRID, clearance=GRID, stub_length=0.25)
    assert canonical(first) == canonical(second)


def test_every_label_sits_on_its_own_wire_after_splitting():
    """A branch split must not copy the trunk label onto the split-off piece."""
    result = route_nets(
        [
            terminal("A", [0, 0], (1, 0)),
            terminal("B", [3, 0], (-1, 0)),
            terminal("C", [1.5, 1], (0, -1)),
        ],
        boxes={},
        grid=GRID,
        clearance=GRID,
        stub_length=0.25,
    )
    assert result["fallbacks"] == []
    labelled = [wire for wire in result["wires"] if wire.get("label")]
    assert len(labelled) == 1
    for wire in result["wires"]:
        if not wire.get("label"):
            continue
        label_xy = wire["label"]["xy"]
        assert any(
            abs(point[0] - label_xy[0]) <= 1e-9 and abs(point[1] - label_xy[1]) <= 1e-9
            for point in wire["points"]
        ), wire
    # exactly one labelled trunk remains and it starts at one of the terminals
    trunk = labelled[0]
    assert trunk["points"][0] in ([0, 0], [1.5, 1], [3, 0])
    assert len(result["wires"]) >= 3


def test_candidate_paths_prefer_fewer_bends_and_stay_on_grid():
    one_bend = orthogonal_candidates([0, 0], [1, 1], 3, GRID)
    assert one_bend[0] == [[0, 0], [0, 1], [1, 1]]
    straight = orthogonal_candidates([0, 0], [0, 1], 3, GRID)
    assert straight[0] == [[0, 0], [0, 1]]
    assert len({canonical(path) for path in straight}) == len(straight)
    distance = orthogonal_candidates([0, 0], [1, 1], 3, GRID)
    assert all(
        abs(point[0] / GRID - round(point[0] / GRID)) < 1e-9
        and abs(point[1] / GRID - round(point[1] / GRID)) < 1e-9
        for path in distance
        for point in path
    )


def test_two_gate_terminals_do_not_retrace_their_initial_escape():
    # Native INV: the old candidate left the gate then retraced the first line
    # before turning, making schCreateWire merge its own path and return nil.
    result = route_nets(
        [terminal("mp", [0.4375, -0.3125], (-1, 0)),
         terminal("mn", [0.4375, -1.9375], (-1, 0))],
        boxes={"mp": [[0.4375, -0.4375], [0.875, -0.125]],
               "mn": [[0.4375, -2.0625], [0.875, -1.75]],
               "foreign_pin": [[-0.3125, -1.1875], [-0.25, -1.0625]]},
        grid=GRID, clearance=0.125, stub_length=0.75,
    )
    assert result["routed_terminals"] == 2, result
    for wire in result["wires"]:
        points = wire["points"]
        assert all(sum((b[k] - a[k]) * (c[k] - b[k]) for k in (0, 1)) >= 0
                   for a, b, c in zip(points, points[1:], points[2:]))
