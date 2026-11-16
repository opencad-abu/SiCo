"""Offline contract tests for template path reuse and bounded fallbacks."""

import copy

import pytest

from cadai.circuit_spec_schema import CircuitSpecError, digest
from cadai.template_catalog import TemplateCatalog
from cadai.template_routing import route_template
from cadai.template_routing_receipt import decode_receipt, receipt

REF = "tpl_" + "1" * 64


def _record():
    capture = {
        "schema": "cad.template.wire-reference.v1",
        "geometry": [
            {
                "device": "d0",
                "library_path": "/pdk",
                "revision": "rev-d0",
                "occupied_bbox": [[-1, -1], [1, 1]],
                "terminals": [{"name": "D", "anchors": [{"id": "a0", "xy": [0, 0]}]}],
            },
            {
                "device": "d1",
                "library_path": "/pdk",
                "revision": "rev-d1",
                "occupied_bbox": [[-1, -1], [1, 1]],
                "terminals": [{"name": "D", "anchors": [{"id": "a0", "xy": [0, 0]}]}],
            },
        ],
        "ports": [],
        "gaps": [],
    }
    return {
        "schema_version": "cad.circuit.template.v2",
        "rule_version": "20260919.capture2.1",
        "template_ref": REF,
        "capture_sha256": "a" * 64,
        "source": {"lib": "src", "cell": "line", "view": "schematic"},
        "topology": {
            "level": "direct",
            "devices": [
                {
                    "id": "d0",
                    "source_name": "M0",
                    "master": {"library": "gpdk045", "cell": "nmos", "view": "symbol"},
                    "role": "device",
                    "kind": "nmos",
                    "attributes": {},
                    "pins": [{"name": "D", "net": "n0"}],
                },
                {
                    "id": "d1",
                    "source_name": "M1",
                    "master": {"library": "gpdk045", "cell": "nmos", "view": "symbol"},
                    "role": "device",
                    "kind": "nmos",
                    "attributes": {},
                    "pins": [{"name": "D", "net": "n0"}],
                },
            ],
            "nets": [{"id": "n0", "source_name": "N"}],
            "ports": [],
            "gaps": [],
        },
        "assets": {
            "schematic": {
                "dbu_per_uu": 1,
                "instances": [
                    {"device": "d0", "relative_xy": [0, 0], "orient": "R0"},
                    {"device": "d1", "relative_xy": [4, 0], "orient": "R0"},
                ],
                "shapes": [
                    {
                        "net": "N",
                        "figure": {
                            "lpp": ["wire", "drawing"],
                            "objType": "line",
                            "points": [[0, 0], [4, 0]],
                        },
                    }
                ],
                "wire_reference": capture,
            }
        },
    }


def _inputs(record, orientation="R0", offset=(10, 5), fallback="stub"):
    from cadai.circuit_geometry_schema import transform

    target_points = {
        name: transform(point, orientation, list(offset))
        for name, point in (("t0", [0, 0]), ("t1", [4, 0]))
    }
    use = {
        "template_ref": record["template_ref"],
        "devices": [
            {"source_device": "d0", "instance": "t0", "master": "m0", "terminal_map": {"D": "D"}},
            {"source_device": "d1", "instance": "t1", "master": "m1", "terminal_map": {"D": "D"}},
        ],
        "net_map": {"n0": "N"},
        "port_map": {},
    }
    plan = {
        "template_use": use,
        "template_evidence": {
            "wire_reference_digest": digest(record["assets"]["schematic"]["wire_reference"])
        },
        "bindings": {
            "masters": [
                {
                    "id": master,
                    "target": {"library": "gpdk045", "cell": "nmos", "view": "symbol"},
                    "library_path": "/pdk",
                }
                for master in ("m0", "m1")
            ]
        },
    }
    geometry = {
        instance: {"occupied_bbox": [[-1, -1], [1, 1]]}
        for instance in ("t0", "t1")
    }
    layout = {
        "grid": 1,
        "clearance": 0,
        "routing": {"mode": "end_to_end", "engine": "template", "fallback": fallback},
        "instances": {
            "t0": {"xy": target_points["t0"], "orientation": orientation},
            "t1": {"xy": target_points["t1"], "orientation": orientation},
        },
    }
    members = [
        {
            "endpoint": {"instance": instance, "terminal": "D"},
            "net": "N",
            "points": [point, transform([1 if instance == "t0" else 3, 0],
                                        orientation, list(offset))],
        }
        for instance, point in target_points.items()
    ]
    placed = {
        instance: {"bbox": [[point[0] - 1, point[1] - 1], [point[0] + 1, point[1] + 1]]}
        for instance, point in target_points.items()
    }
    pins = [
        {"endpoint": member["endpoint"], "net": "N", "xy": member["points"][0]}
        for member in members
    ]
    return plan, geometry, layout, members, placed, pins


def run(record, monkeypatch, **kwargs):
    monkeypatch.setattr(TemplateCatalog, "get", lambda self, ref: record)
    inputs = _inputs(record, **kwargs)
    return inputs, route_template(*inputs[0:1], *inputs[1:], workspace=None)


@pytest.mark.parametrize("orientation", ["R0", "R90", "MY"])
def test_rigid_translation_rotation_and_mirror_reuse(monkeypatch, orientation):
    record = _record()
    inputs, (wires, report) = run(record, monkeypatch, orientation=orientation)
    assert wires and all(row["status"] == "reused" for row in report["nets"]), report
    assert report["reused_nets"] == 1 and report["rerouted_nets"] == 0
    assert report["metrics"]["end_to_end_ratio"] == 1.0
    assert len(wires) == 1
    assert wires[0]["points"] == [inputs[3][0]["points"][0], inputs[3][1]["points"][0]]


@pytest.mark.parametrize(
    "mutation, reason",
    [
        ("master", "source_target_geometry_not_rigid_compatible"),
        ("path", "source_target_geometry_not_rigid_compatible"),
        ("bbox", "source_target_geometry_not_rigid_compatible"),
        ("endpoint", "source_target_geometry_not_rigid_compatible"),
    ],
)
def test_rigid_identity_guards_fall_back_per_net(monkeypatch, mutation, reason):
    record = _record()
    inputs = _inputs(record)
    plan, geometry, layout, members, placed, pins = inputs
    if mutation == "master":
        plan["bindings"]["masters"][0]["target"]["cell"] = "pmos"
    elif mutation == "path":
        plan["bindings"]["masters"][0]["library_path"] = "/other-pdk"
    elif mutation == "bbox":
        geometry["t1"]["occupied_bbox"] = [[-2, -1], [2, 1]]
    else:
        members[1]["points"][0] = [5, 5]
        pins[1]["xy"] = [5, 5]
    monkeypatch.setattr(TemplateCatalog, "get", lambda self, ref: record)
    wires, report = route_template(plan, geometry, layout, members, placed, pins)
    row = report["nets"][0]
    assert row["status"] == "stub" and reason in row["reasons"]
    assert wires == members


def test_route_report_keeps_bounded_deformation_diagnostics(monkeypatch):
    record = _record()
    plan, geometry, layout, members, placed, pins = _inputs(record)
    layout["instances"]["t1"]["xy"] = [12, 5]
    placed["t1"]["bbox"] = [[11, 4], [13, 6]]
    monkeypatch.setattr(TemplateCatalog, "get", lambda self, ref: record)
    _, report = route_template(plan, geometry, layout, members, placed, pins)
    row = report["nets"][0]
    assert row["status"] == "stub"
    assert len(row["diagnostics"]) <= 32
    assert all(set(item) <= {"code", "axis", "source", "targets", "target", "grid",
                             "tracks", "endpoints", "reasons"}
               for item in row["diagnostics"])


def test_off_grid_collision_and_coverage_use_stub_or_reject(monkeypatch):
    record = _record()
    plan, geometry, layout, members, placed, pins = _inputs(record)
    layout["grid"] = 2
    monkeypatch.setattr(TemplateCatalog, "get", lambda self, ref: record)
    _, report = route_template(plan, geometry, layout, members, placed, pins)
    assert report["nets"][0]["status"] == "stub"
    assert "template_path_off_grid" in report["nets"][0]["reasons"]

    layout["grid"] = 1
    placed["blocker"] = {"bbox": [[11, 4], [13, 6]]}
    _, report = route_template(plan, geometry, layout, members, placed, pins)
    assert report["nets"][0]["status"] == "stub"
    assert "template_path_hits_instance" in report["nets"][0]["reasons"]

    members.pop()
    pins.pop()
    _, report = route_template(plan, geometry, layout, members, placed, pins)
    assert report["nets"][0]["status"] == "stub"
    assert "source_target_endpoint_coverage_differs" in report["nets"][0]["reasons"]

    layout["routing"]["fallback"] = "reject"
    _, report = route_template(plan, geometry, layout, members, placed, pins)
    assert report["nets"][0]["status"] == "rejected"
    assert report["issues"]


def test_digest_change_source_corruption_and_receipt_are_fail_closed(monkeypatch):
    record = _record()
    plan, geometry, layout, members, placed, pins = _inputs(record)
    changed = copy.deepcopy(record)
    changed["assets"]["schematic"]["wire_reference"]["geometry"][0]["revision"] = "changed"
    monkeypatch.setattr(TemplateCatalog, "get", lambda self, ref: changed)
    with pytest.raises(CircuitSpecError, match="wire reference changed"):
        route_template(plan, geometry, layout, members, placed, pins)

    malformed = copy.deepcopy(record)
    malformed["assets"]["schematic"]["wire_reference"]["geometry"] = []
    # A changed reference is rejected before physical routing. An incomplete
    # geometry catalog is retained as a bounded per-net failure, never as a
    # usable fallback hint.
    plan["template_evidence"]["wire_reference_digest"] = digest(
        malformed["assets"]["schematic"]["wire_reference"]
    )
    monkeypatch.setattr(TemplateCatalog, "get", lambda self, ref: malformed)
    _, report = route_template(plan, geometry, layout, members, placed, pins)
    assert report["nets"][0]["status"] == "stub"
    assert "source_pin_anchor_missing_or_ambiguous" in report["nets"][0]["reasons"]

    monkeypatch.setattr(TemplateCatalog, "get", lambda self, ref: record)
    plan["template_evidence"]["wire_reference_digest"] = digest(
        record["assets"]["schematic"]["wire_reference"]
    )
    _, report = route_template(plan, geometry, layout, members, placed, pins)
    row = receipt({"routing": report, "wires": members})
    decoded = decode_receipt(row)
    assert decoded["template_ref"] == REF
    assert decoded["nets"][0]["status"] == "reused"
    assert "library_path" not in str(decoded)


def test_v1_template_identity_and_projected_catalog_privacy(tmp_path, monkeypatch):
    from cadai.template_project import project_record

    record = _record()
    legacy = copy.deepcopy(record)
    legacy["schema_version"] = "cad.circuit.template.v2"
    projected = project_record(legacy, "L2")
    text = str(projected)
    assert projected["schema_version"] == legacy["schema_version"]
    assert "wire_reference" not in text and "library_path" not in text
    assert record["template_ref"] == REF


def test_owner_exemption_rejects_inward_body_crossing_but_allows_boundary_tangent():
    from cadai.template_route_paths import path_gaps, wire_paths

    members = [{"endpoint": {"instance": "i", "terminal": "D"}, "net": "N",
                "points": [[0, 1], [0, 2]]},
               {"endpoint": {"port": "P"}, "net": "N", "points": [[0, -2], [0, -3]]}]
    placed = {"i": {"bbox": [[-1, -1], [1, 1]]}}
    edges = [{"shape": 0, "segment": 0, "points": [[0, 1], [0, -2]]}]
    paths = wire_paths(edges, members, "N")
    layout = {"grid": 1, "clearance": 0}
    assert "template_path_crosses_owner_body" in path_gaps(paths, members, layout, placed, [])
    members[1]["points"] = [[2, 1], [3, 1]]
    edges[0]["points"] = [[0, 1], [2, 1]]
    assert not path_gaps(wire_paths(edges, members, "N"), members, layout, placed, [])


def test_template_metrics_count_split_corners_without_counting_t_junctions():
    from cadai.template_route_paths import path_bends

    paths = [{"net": "N", "template_segment": True, "points": points} for points in
             [[[0, 0], [1, 0]], [[1, 0], [1, 2]], [[1, 2], [2, 2]],
              [[1, 2], [0, 2]]]]
    assert path_bends(paths, 1) == 1


@pytest.mark.parametrize("reasons", ["bad", 1, {}, [None]])
def test_receipt_rejects_malformed_reasons(reasons):
    row = ["cad.template.routing-receipt.v1", REF, "a"*64, "b"*64, "c"*64, "d"*64,
           [["N", "reused", 2, reasons]]]
    with pytest.raises(CircuitSpecError, match="invalid"):
        decode_receipt(row)
    row[-1][0][-1] = None
    assert decode_receipt(row)["nets"][0]["reasons"] == []
