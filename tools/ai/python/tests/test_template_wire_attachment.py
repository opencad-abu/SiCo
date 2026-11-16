import pytest

from cadai.circuit_spec_schema import CircuitSpecError
from cadai.template_capture_geometry import validate_geometry_capture
from cadai.template_route_paths import path_gaps, repair_branches
from cadai.template_routing_receipt import decode_receipt
from cadai.template_schema import TemplateError
from cadai.template_wire_attachment import compile_reference
from cadai.template_wire_graph import connect_net


def segment(a, b, shape=0):
    return {"shape": shape, "segment": 0, "points": [a, b]}


def anchor(xy, endpoint="M0"):
    return {"xy": xy, "endpoint": {"device": endpoint, "terminal": "D"}}


def test_t_junction_is_split_and_connected():
    result = connect_net(
        [segment([0, 0], [10, 0]), segment([5, 0], [5, 5], 1)],
        [anchor([0, 0], "M0"), anchor([10, 0], "M1"), anchor([5, 5], "M2")],
    )
    assert result["gaps"] == []
    assert [edge["points"] for edge in result["edges"]] == [
        [[0, 0], [5, 0]],
        [[5, 0], [10, 0]],
        [[5, 0], [5, 5]],
    ]
    assert result["junctions"] == [[5, 0]]
    assert len(result["components"]) == 1


def test_interior_crossing_is_ambiguous_even_on_one_net():
    result = connect_net(
        [segment([0, 0], [10, 0]), segment([5, -5], [5, 5], 1)],
        [anchor([0, 0]), anchor([10, 0], "M1")],
    )
    assert "ambiguous_source_crossing" in result["gaps"]


def test_same_net_overlap_is_not_silently_deduplicated():
    result = connect_net(
        [segment([0, 0], [10, 0]), segment([5, 0], [15, 0], 1)],
        [anchor([0, 0]), anchor([15, 0], "M1")],
    )
    assert "overlapping_source_wire" in result["gaps"]


def test_disconnected_shapes_are_not_bridged_by_a_label():
    result = connect_net(
        [segment([0, 0], [1, 0]), segment([5, 0], [6, 0], 1)],
        [anchor([0, 0]), anchor([6, 0], "M1")],
    )
    assert "source_net_disconnected" in result["gaps"]
    assert "open_source_wire_end" in result["gaps"]


def v2_rows(observation=None):
    observation = observation or {
        "ok": True,
        "occupied_bbox": [[-1, -1], [1, 1]],
        "pin_centers": [["D", [[1, 0]]]],
        "signature": "static-master",
    }
    return [
        {
            "kind": "instance",
            "name": "M0",
            "libName": "gpdk045",
            "cellName": "nmos2v",
            "viewName": "symbol",
            "terminal_names": ["D"],
        },
        {"kind": "terminal", "name": "OUT"},
        {
            "kind": "instance_geometry",
            "instance": "M0",
            "library_path": "/pdk",
            "observation": observation,
        },
        {"kind": "port_anchor", "terminal": "OUT", "anchor_id": "a0", "xy": [0, 0]},
        {"kind": "dependency_guard", "before": "snapshot", "after": "snapshot"},
    ]


def test_capture_v2_rejects_failed_observation_with_extra_fields():
    rows = v2_rows({"ok": False, "code": "missing", "message": "no symbol", "signature": "guess"})
    with pytest.raises(TemplateError, match="failed geometry"):
        validate_geometry_capture({"asset": "schematic", "dbu_per_uu": 1000}, rows)


def test_capture_v2_requires_every_non_basic_instance_geometry():
    rows = v2_rows()
    rows.pop(2)
    with pytest.raises(TemplateError, match="every instance geometry"):
        validate_geometry_capture({"asset": "schematic", "dbu_per_uu": 1000}, rows)


def test_capture_accepts_optional_annotation_proxy_but_rejects_unknown_fields():
    rows = v2_rows()
    observation = next(r["observation"] for r in rows if r["kind"] == "instance_geometry")
    observation["annotation_bbox"] = [[-2, -2], [2, 2]]
    validate_geometry_capture({"asset": "schematic", "dbu_per_uu": 1000}, rows)
    observation["rendered_text_verified"] = True
    with pytest.raises(TemplateError, match="observation fields"):
        validate_geometry_capture({"asset": "schematic", "dbu_per_uu": 1000}, rows)


def test_routing_receipt_is_strict_and_bounded():
    with pytest.raises(CircuitSpecError):
        decode_receipt(["cad.template.routing-receipt.v0"])
    good = [
        "cad.template.routing-receipt.v1", "tpl_" + "a" * 64, "b" * 64,
        "c" * 64, "d" * 64, "e" * 64,
        [["N", "reused", 2, []]],
    ]
    assert decode_receipt(good)["nets"][0]["terminals"] == 2
    for bad in (
        [*good[:6], [["N", "unknown", 2, []]]],
        [*good[:6], [["N", "reused", True, []]]],
        [*good[:6], [["N", "reused", 2, ["x" * 257]]]],
        [*good[:6], [["N", "reused", 2, []], ["N", "stub", 1, []]]],
    ):
        with pytest.raises(CircuitSpecError):
            decode_receipt(bad)


def _attachment_record():
    def geometry(device, terminal="D"):
        return {
            "device": device,
            "library_path": "/pdk",
            "revision": "rev",
            "occupied_bbox": [[-1, -1], [1, 1]],
            "terminals": [{"name": terminal, "anchors": [{"id": "a0", "xy": [0, 0]}]}],
        }

    return {
        "assets": {
            "schematic": {
                "dbu_per_uu": 1,
                "instances": [
                    {"device": "d0", "relative_xy": [0, 0], "orient": "R0"},
                    {"device": "d1", "relative_xy": [10, 0], "orient": "R0"},
                ],
                "shapes": [
                    {"net": "N0", "figure": {"lpp": ["wire", "drawing"],
                     "objType": "line", "points": [[0, 0], [5, 0]]}},
                    {"net": "N1", "figure": {"lpp": ["wire", "drawing"],
                     "objType": "line", "points": [[10, 0], [15, 0]]}},
                ],
                "wire_reference": {
                    "schema": "cad.template.wire-reference.v1",
                    "geometry": [geometry("d0"), geometry("d1")],
                    "ports": [
                        {"port": "P0", "id": "p0", "xy": [5, 0]},
                        {"port": "P1", "id": "p1", "xy": [15, 0]},
                    ],
                    "gaps": [{"code": "device_geometry_unavailable", "device": "d0"}],
                },
            }
        },
        "topology": {
            "devices": [
                {"id": "d0", "pins": [{"name": "D", "net": "n0"}]},
                {"id": "d1", "pins": [{"name": "D", "net": "n1"}]},
            ],
            "nets": [
                {"id": "n0", "source_name": "N0"},
                {"id": "n1", "source_name": "N1"},
            ],
            "ports": [
                {"name": "P0", "net": "n0"}, {"name": "P1", "net": "n1"}
            ],
        },
    }


def test_compile_reference_scopes_capture_gaps_to_the_affected_net():
    result = compile_reference(_attachment_record())
    assert "device_geometry_unavailable" in result["nets"]["n0"]["gaps"]
    assert "device_geometry_unavailable" not in result["nets"]["n1"]["gaps"]


def test_over_budget_wire_evidence_stays_a_gap(monkeypatch):
    from cadai import template_wire_attachment as attachment
    from cadai.template_wire_contacts import WireBudgetExceeded

    def boom(*args, **kwargs):
        raise WireBudgetExceeded("source exceeds 2048 wire segments")

    monkeypatch.setattr(attachment, "source_contacts", boom)
    result = attachment.compile_reference(_attachment_record())
    assert result["nets"]
    assert all(
        "source_wire_evidence_over_budget" in row["gaps"]
        for row in result["nets"].values()
    )
    assert all(not row["eligible"] for row in result["nets"].values())


def test_compile_reference_rejects_malformed_catalog_without_keyerror():
    bad = _attachment_record()
    bad["assets"]["schematic"]["wire_reference"]["geometry"][0]["occupied_bbox"] = []
    with pytest.raises(TemplateError, match="routing_reference_malformed"):
        compile_reference(bad)


def test_repair_branches_repairs_one_moved_degree_one_terminal():
    source = {
        "dbu_per_uu": 1,
        "positions": {
            "d0": {"relative_xy": [0, 0], "orient": "R0"},
            "d1": {"relative_xy": [2, 0], "orient": "R0"},
        },
    }
    use = {"port_map": {}, "devices": [
        {"source_device": "d0", "instance": "t0", "terminal_map": {"D": "D"}},
        {"source_device": "d1", "instance": "t1", "terminal_map": {"D": "D"}},
    ]}
    members = [
        {"endpoint": {"instance": "t0", "terminal": "D"}, "net": "N",
         "points": [[0, 0], [0.25, 0]]},
        {"endpoint": {"instance": "t1", "terminal": "D"}, "net": "N",
         "points": [[2.5, 0], [2.75, 0]]},
    ]
    src = {
        "anchors": [
            {"endpoint": {"device": "d0", "terminal": "D"}, "xy": [0, 0]},
            {"endpoint": {"device": "d1", "terminal": "D"}, "xy": [2, 0]},
        ],
        "edges": [{"points": [[0, 0], [2, 0]], "shape": 0, "segment": 0}],
        "gaps": [],
    }
    layout = {
        "grid": 0.25,
        "routing": {"max_bends": 3, "max_detour": 20},
        "clearance": 0,
        "instances": {
            "t0": {"xy": [0, 0], "orientation": "R0"},
            "t1": {"xy": [2.5, 0], "orientation": "R0"},
        },
    }
    placed = {}
    pins = [{"endpoint": m["endpoint"], "net": "N", "xy": m["points"][0]} for m in members]
    result = repair_branches(src, source, use, members, layout, placed, pins, [])
    assert isinstance(result, tuple) and len(result) == 2
    paths, frame = result
    assert frame == ("R0", [0.5, 0.0])
    assert paths[-1]["points"] == [[0.25, 0], [2.5, 0.0]]
    assert not path_gaps(paths, members, layout, placed, pins, [])


def test_repair_branches_rejects_two_moved_degree_one_terminals_without_throwing():
    source = {
        "dbu_per_uu": 1,
        "positions": {
            "d0": {"relative_xy": [0, 0], "orient": "R0"},
            "d1": {"relative_xy": [2, 0], "orient": "R0"},
        },
    }
    use = {"port_map": {}, "devices": [
        {"source_device": "d0", "instance": "t0", "terminal_map": {"D": "D"}},
        {"source_device": "d1", "instance": "t1", "terminal_map": {"D": "D"}},
    ]}
    members = [
        {"endpoint": {"instance": "t0", "terminal": "D"}, "net": "N",
         "points": [[0, 0], [0.25, 0]]},
        {"endpoint": {"instance": "t1", "terminal": "D"}, "net": "N",
         "points": [[3, 0], [3.25, 0]]},
    ]
    src = {
        "anchors": [
            {"endpoint": {"device": "d0", "terminal": "D"}, "xy": [0, 0]},
            {"endpoint": {"device": "d1", "terminal": "D"}, "xy": [2, 0]},
        ],
        "edges": [{"points": [[0, 0], [2, 0]], "shape": 0, "segment": 0}],
        "gaps": [],
    }
    layout = {
        "grid": 0.25,
        "routing": {"max_bends": 3, "max_detour": 20},
        "clearance": 0,
        "instances": {
            "t0": {"xy": [0.5, 0], "orientation": "R0"},
            "t1": {"xy": [2.5, 0], "orientation": "R0"},
        },
    }
    placed = {}
    pins = [{"endpoint": m["endpoint"], "net": "N", "xy": m["points"][0]} for m in members]
    assert repair_branches(src, source, use, members, layout, placed, pins, []) is None
