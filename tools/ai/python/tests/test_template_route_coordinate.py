"""Neighbor repairs commit as a group only after a complete final-net check."""

import copy

import pytest

from cadai import template_route_coordinate as coordinate
from cadai.circuit_geometry_schema import MATRICES, transform
from cadai.template_route_paths import path_gaps, wire_paths


@pytest.fixture(autouse=True)
def isolate_simple_coordination(monkeypatch):
    """Network fallback has its own integration/rollback suite."""
    monkeypatch.setattr(coordinate, "recover_networks", lambda *a: None)


def setup(orientation="R0"):
    def point(p):
        return transform(p, orientation)
    def member(net, i, p):
        return dict(net=net, endpoint=dict(instance=f"{net}{i}", terminal="p"),
                    points=[point(p), point([p[0], p[1]+.5])])
    members = dict(A=[member("A", i, p) for i, p in enumerate([[-3, 0], [3, 0]])],
                   B=[member("B", i, p) for i, p in enumerate([[0, 1], [0, 3]])])
    def wires(net, points):
        edges = [dict(shape=i, segment=0, points=[point(a), point(b)])
                 for i, (a, b) in enumerate(zip(points, points[1:]))]
        return wire_paths(edges, members[net], net)
    a = wires("A", [[-3, 0], [-3, 2], [3, 2], [3, 0]])
    b = wires("B", [[0, 1], [0, 3]])
    return (dict(A=a, B=members["B"]),
            [dict(net="A", status="reused", reasons=[], transform={}),
             dict(net="B", status="stub", reasons=["collision"])],
            members, {}, dict(B=b), dict(grid=.5, clearance=0, routing={}), {}, [])


@pytest.mark.parametrize("orientation", list(MATRICES))
def test_pending_graph_and_neighbor_are_committed_atomically(orientation):
    args = setup(orientation)
    before = copy.deepcopy(args)
    coordinate.coordinate_routes(*args)
    assert [r["method"] for r in args[1]] == ["coordinated_branch_chain", "coordinated_deformation"]
    assert args[0]["B"] == args[4]["B"]
    assert args[0]["A"] != before[0]["A"]
    assert "transform" not in args[1][0]
    assert args[2:] == before[2:]
    for net in ("A", "B"):
        assert not path_gaps(args[0][net], args[2][net], args[5], args[6], args[7],
                             args[0]["A"]+args[0]["B"])


@pytest.mark.parametrize("failure", ["repair", "late_check", "port", "unverified_neighbor",
                                     "budget", "no_pending", "invalid_pending"])
def test_failure_keeps_original_routes_and_statuses(monkeypatch, failure):
    args = setup()
    if failure == "repair":
        monkeypatch.setattr(coordinate, "chain_paths", lambda *a: None)
    elif failure == "late_check":
        real = coordinate.path_gaps
        monkeypatch.setattr(coordinate, "path_gaps", lambda *a: ["late_failure"] if len(a)>5
                            else real(*a))
    elif failure == "port":
        args[3]["blocked"] = [dict(net="port", points=[[0, 1.5], [0, 2.5]])]
    elif failure == "unverified_neighbor":
        args[1][0]["status"] = "stub"
    elif failure == "budget":
        monkeypatch.setattr(coordinate, "MAX_PENDING", 0)
    elif failure == "no_pending":
        args[4].clear()
    else:
        args[4]["B"][0]["points"][0] = [0, 99]
    before = copy.deepcopy(args)
    coordinate.coordinate_routes(*args)
    assert args[0] == before[0]
    assert [r["status"] for r in args[1]] == [r["status"] for r in before[1]]
    assert args[2:] == before[2:]


def test_second_neighbor_failure_rolls_back_first_repair(monkeypatch):
    args = setup()
    args[0]["C"] = copy.deepcopy(args[0]["A"])
    args[2]["C"] = copy.deepcopy(args[2]["A"])
    args[1].append(dict(net="C", status="reused", reasons=[]))
    for row in args[0]["C"] + args[2]["C"]:
        row["net"] = "C"
    calls = []
    def repair(*a):
        net = a[1][0]["net"]
        calls.append(net)
        return args[0][net] if len(calls) == 1 else None
    monkeypatch.setattr(coordinate, "chain_paths", repair)
    before = copy.deepcopy(args[0])
    coordinate.coordinate_routes(*args)
    assert calls == ["A", "C"] and args[0] == before


def test_neighbor_budget_refuses_before_any_search(monkeypatch):
    args = setup()
    monkeypatch.setattr(coordinate, "MAX_NEIGHBORS", 0)
    coordinate.coordinate_routes(*args)
    assert args[1][-1]["diagnostics"][-1]["reasons"] == ["coordinated_neighbor_limit"]
