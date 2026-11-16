"""Annotation reservations keep labels readable without bypassing electrical checks."""

import copy

from circuit_geometry_fixture import sample_geometry

from cadai.circuit_geometry import preview_geometry
from cadai.circuit_stub_labels import reserve_stub_labels


def test_mirrored_stub_reserves_text_space_and_preserves_endpoint_and_net():
    wire = dict(endpoint=dict(instance="M", terminal="B"), net="VSS",
                points=[[-0.25, 0], [-0.75, 0]], label=dict(text="VSS", xy=[-0.75, 0]))
    original = copy.deepcopy(wire)
    sources = {"M": {"annotation_bbox": [[-0.1, -0.5], [1.0, 0.5]]}}
    layout = dict(grid=0.0625, instances={"M": dict(xy=[0, 0], orientation="MY")})
    result = reserve_stub_labels([wire], sources, layout)[0]
    assert result["points"] == [[-0.25, 0], [-1.125, 0]]
    assert result["label"]["xy"] == [-1.125, 0]
    assert result["net"] == wire["net"] and result["endpoint"] == wire["endpoint"]
    assert wire == original
    for kind in ("route", "cadence_route"):
        routed = {**wire, "kind": kind}
        assert reserve_stub_labels([routed], sources, layout) == [routed]
    linked = {**wire, "end_endpoint": dict(instance="other", terminal="P")}
    assert reserve_stub_labels([linked], sources, layout) == [linked]


def test_extended_stub_still_rejects_other_net_contacts_and_changes_digest():
    args = sample_geometry()
    before = preview_geometry(**args)
    source = next(g for g in args["geometry"]["instances"] if g["instance"] == "dut")
    source["annotation_bbox"] = [[-0.5, -0.5], [5, 0.5]]
    after = preview_geometry(**args)
    assert after["geometry_plan_digest"] != before["geometry_plan_digest"]
    assert not after["plan"]["geometry_checks_passed"]
    assert any(r["code"] == "stub_hits_instance" for r in after["plan"]["issues"])
    assert after["plan"]["label_rendering_verified"] is False
