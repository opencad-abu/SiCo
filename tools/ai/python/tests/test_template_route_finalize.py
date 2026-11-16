"""Final reconciliation must preserve safety after repairs and later fallbacks."""

import copy

import pytest
from test_template_route_deform import bus_case, deformed

from cadai import template_route_finalize as finalize
from cadai.circuit_geometry_schema import MATRICES, transform
from cadai.template_route_detour import detour_paths
from cadai.template_route_paths import path_gaps


def setup(orientation="R0", fallback="reject"):
    record, inputs = bus_case(orientation=orientation)
    _, _, layout, members, placed, pins = inputs
    layout["routing"]["fallback"] = fallback
    paths = deformed(record, inputs)
    obstacles = [dict(net="other", endpoint=dict(port=f"P{i}"), points=[
        transform(p, orientation, [10, 20]) for p in pair]) for i, pair in enumerate([
            [[5, 1], [5, 3]], [[22, 1], [22, 3]]])]
    planned = dict(rail=paths)
    report = [dict(net="rail", status="rerouted", method="orthogonal_deformation",
                   terminals=8, segments=len(paths), reasons=[])]
    return planned, report, dict(rail=members), dict(other=obstacles), layout, placed, pins


@pytest.mark.parametrize("orientation", list(MATRICES))
def test_complete_graph_repairs_two_collisions_with_port_stubs(orientation):
    args = setup(orientation)
    old = copy.deepcopy(args)
    finalize.finalize_routes(*args)
    planned, report, by_net, ports, layout, placed, pins = args
    assert report[0]["method"] == "final_graph_detour"
    assert report[0]["segments"] == old[1][0]["segments"] + 4
    assert "transform" not in report[0]
    assert len(report[0]["diagnostics"][0]["source_edges"]) == 2
    assert not path_gaps(planned["rail"], by_net["rail"], layout, placed, pins, ports["other"])
    assert args[2:] == old[2:]


@pytest.mark.parametrize("fallback,status", [("reject", "rejected"), ("stub", "stub")])
def test_repair_budget_keeps_original_fallback(monkeypatch, fallback, status):
    args = setup(fallback=fallback)
    monkeypatch.setattr(finalize, "MAX_NET_REPAIRS", 0)
    finalize.finalize_routes(*args)
    assert args[0]["rail"] == args[2]["rail"]
    assert args[1][0]["status"] == status
    assert args[1][0]["diagnostics"][0]["code"] == "final_net_repair_budget_exhausted"
    assert not {"method", "transform", "segments"} & set(args[1][0])


def test_failed_multi_edge_repair_cannot_leak_a_partial_result(monkeypatch):
    from cadai import template_route_components as components

    args = setup()
    args[4]["routing"]["max_detour"] = 1
    # Pin the bus so the track fallback cannot legally shorten it around the
    # obstacle after fixed-vertex detours exhaust their length budget.
    args[2]["rail"].append(dict(net="rail", endpoint=dict(instance="middle", terminal="p"),
                               points=[[17, 22], [17, 22.5]]))
    # The new component solver can reconnect seven branches at unchanged
    # length; exhaust it after a partial combination to verify rollback.
    monkeypatch.setattr(components, "MAX_STEPS", 2)
    finalize.finalize_routes(*args)
    assert args[0]["rail"] == args[2]["rail"]
    assert args[1][0]["status"] == "rejected"
    assert args[1][0]["diagnostics"][-1]["candidates"] == 2


def test_conflict_component_rechecks_complete_final_wires():
    args = setup()
    args[4]["routing"]["max_detour"] = 1
    args[2]["rail"].append(dict(net="rail", endpoint=dict(instance="middle", terminal="p"),
                               points=[[17, 22], [17, 22.5]]))
    before = copy.deepcopy(args)
    finalize.finalize_routes(*args)
    assert args[1][0]["method"] == "final_conflict_component"
    assert args[1][0]["diagnostics"][-1]["branches"] == 7
    assert not path_gaps(args[0]["rail"], args[2]["rail"], args[4], args[5], args[6],
                         args[3]["other"])
    assert args[2:] == before[2:]


def test_unverified_stub_is_never_upgraded_by_finalizer():
    args = setup()
    args[0]["rail"] = args[2]["rail"]
    args[1][0].update(status="stub", reasons=["source_attachment_gaps"])
    old = copy.deepcopy(args)
    finalize.finalize_routes(*args)
    assert args == old


def test_later_fallback_rechecks_a_prior_repair_and_cannot_cycle(monkeypatch):
    args = setup(fallback="stub")
    planned, report, by_net, _, _, _, _ = args
    planned["late"] = [dict(net="late", template_segment=True, owners=[],
                            template_source=dict(shape=0, segment=0),
                            endpoint=dict(instance="late", terminal="p"),
                            points=[[50, 0], [51, 0]])]
    by_net["late"] = [dict(planned["late"][0], points=[[52, 0], [53, 0]])]
    report.append(dict(net="late", status="reused", reasons=[], terminals=1))
    calls = []

    def check(paths, *unused):
        net = paths[0]["net"]
        return (["template_path_hits_instance"] if net == "late" else
                ["template_paths_different_nets_touch"])

    def repair(edges, members, layout, placed, pins, reserved, diagnostics):
        calls.append(members[0]["net"])
        diagnostics.append(dict(code="target_local_detour_applied"))
        return planned["rail"]

    monkeypatch.setattr(finalize, "path_gaps", check)
    monkeypatch.setattr(finalize, "repair_paths", repair)
    finalize.finalize_routes(*args)
    assert calls == ["rail"]
    assert all(row["status"] == "stub" for row in report)
    assert planned == by_net


def test_repair_uses_latest_wires_after_an_earlier_fallback(monkeypatch):
    args = setup()
    planned, report, by_net, _, _, _, _ = args
    stale = dict(net="first", points=[[0, 0], [1, 0]])
    stub = dict(net="first", endpoint=dict(instance="first", terminal="p"),
                points=[[0, 0], [0, 1]])
    planned["first"], by_net["first"] = [stale], [stub]
    report.insert(0, dict(net="first", status="reused", terminals=1, reasons=[]))
    checked = []

    def check(paths, *unused):
        return (["template_path_hits_instance"] if paths[0]["net"] == "first" else
                ["template_paths_different_nets_touch"])

    def repair(edges, members, layout, placed, pins, reserved, diagnostics):
        checked.append([w for w in reserved if w["net"] == "first"])
        return None

    monkeypatch.setattr(finalize, "path_gaps", check)
    monkeypatch.setattr(finalize, "repair_paths", repair)
    finalize.finalize_routes(*args)
    assert checked == [[stub]]


def test_multi_edge_solver_checks_backtracking_and_global_budget(monkeypatch):
    from cadai import template_route_detour as detour

    args = setup()
    planned, _, by_net, ports, layout, placed, pins = args
    edges = [dict(w["template_source"], points=w["points"]) for w in planned["rail"]]
    original = detour.orthogonal_candidates
    first, second = [10, 22], [26, 22]
    calls = []

    def candidates(start, end, *unused):
        calls.append(start)
        if start == first:
            return [[start, [10, 24], [17, 24], end],
                    [start, [10, 25], [17, 25], end]]
        assert start == second
        return original(start, end, 3, .5, dict(x=[], y=[24]))

    real_graph = detour.graph_preserved

    def graph(trial, anchors, count, grid):
        # Model an interaction that rules out the first complete combination.
        if len(trial) == len(edges) + 4 and [10, 24] in [p for e in trial for p in e["points"]]:
            return False
        return real_graph(trial, anchors, count, grid)

    monkeypatch.setattr(detour, "orthogonal_candidates", candidates)
    monkeypatch.setattr(detour, "graph_preserved", graph)
    diagnostics = []
    result = detour_paths(edges, by_net["rail"], layout, placed, pins, ports["other"], diagnostics)
    assert result and calls.count(second) == 2
    assert diagnostics[0]["candidates"] <= detour.MAX_CANDIDATES


def test_legal_first_edge_is_discarded_when_second_has_no_candidate(monkeypatch):
    from cadai import template_route_detour as detour

    args = setup()
    planned, _, by_net, ports, layout, placed, pins = args
    edges = [dict(w["template_source"], points=w["points"]) for w in planned["rail"]]
    old = copy.deepcopy(edges)

    def candidates(start, end, *unused):
        return ([[start, [10, 24], [17, 24], end]] if start == [10, 22] else [])

    monkeypatch.setattr(detour, "orthogonal_candidates", candidates)
    diagnostics = []
    assert detour_paths(edges, by_net["rail"], layout, placed, pins,
                        ports["other"], diagnostics) is None
    assert edges == old
    assert diagnostics[0]["candidates"] == 1
    assert diagnostics[0]["code"] == "target_local_detour_exhausted"
