"""Only proven, body-safe deformations may be retained for final coordination."""

import pytest
from test_template_route_deform import bus_case
from test_template_route_detour import foreign

from cadai import template_route_deform as deform
from cadai.template_wire_attachment import compile_reference


@pytest.mark.parametrize("failure", ["source", "identity", "anchor", "body", "reverse", "wire"])
def test_pending_capture_occurs_only_after_all_non_wire_guards(monkeypatch, failure):
    record, inputs = bus_case()
    plan, geometry, layout, members, placed, pins = inputs
    source = compile_reference(record)
    src = source["nets"]["n0"]
    if failure == "source":
        src["gaps"] = ["unproved"]
    elif failure == "identity":
        members.pop()
    elif failure == "anchor":
        geometry["target0"]["terminals"] = []
    elif failure == "body":
        placed["obstacle"] = dict(bbox=[[14, 21], [16, 23]])
    elif failure == "reverse":
        monkeypatch.setattr(deform, "_axis_map", lambda *a, **kw: None)
    monkeypatch.setattr(deform, "repair_paths", lambda *a: None)
    pending = []
    assert deform.deform_paths(src, source, plan["template_use"], members, geometry,
                               layout, placed, pins, foreign(), [], pending=pending) is None
    assert bool(pending) == (failure == "wire")


def test_pending_budget_is_visible_in_public_report(monkeypatch):
    from cadai import template_routing as routing
    from cadai.template_catalog import TemplateCatalog

    record, inputs = bus_case()
    monkeypatch.setattr(TemplateCatalog, "get", lambda *a: record)
    monkeypatch.setattr(deform, "repair_paths", lambda *a: None)
    monkeypatch.setattr(routing, "MAX_PENDING", 0)
    inputs[2]["routing"].update(ports="route", fallback="stub")
    inputs[3].append(dict(net="ahead", endpoint=dict(port="P"), points=[[15, 21], [15, 23]]))
    _, report = routing.route_template(*inputs)
    row = next(r for r in report["nets"] if r["net"] == "rail")
    assert row["status"] == "stub"
    assert row["diagnostics"][-1]["code"] == "coordinated_pending_budget_exhausted"


@pytest.mark.parametrize("budget", [0, 8])
def test_final_fallback_retains_complete_proved_graph_not_stubs(monkeypatch, budget):
    from test_template_routing import _inputs, _record

    from cadai import template_routing as routing
    from cadai.template_catalog import TemplateCatalog

    record = _record()
    inputs = _inputs(record)
    monkeypatch.setattr(TemplateCatalog, "get", lambda *a: record)
    monkeypatch.setattr(routing, "MAX_PENDING", budget)
    captured = []
    def fallback(planned, report, *unused):
        assert report[0]["status"] == "reused"
        captured.extend(planned["N"])
        report[0].update(status="stub", reasons=["template_paths_different_nets_touch"])
        planned["N"] = inputs[3]
    def coordinate(planned, report, by_net, ports, pending, *unused):
        assert planned["N"] == inputs[3]
        assert pending == (dict(N=captured) if budget else {})
        if budget:
            assert all(w["template_segment"] for w in pending["N"])
        else:
            assert report[0]["diagnostics"][-1]["code"] == "coordinated_pending_budget_exhausted"
    monkeypatch.setattr(routing, "finalize_routes", fallback)
    monkeypatch.setattr(routing, "coordinate_routes", coordinate)
    routing.route_template(*inputs)
