"""Deterministic relations-driven placement: rows, mirrored pairs and port bands."""

from __future__ import annotations

import json
from collections import Counter

import pytest
from test_circuit_templates import make_catalog

from cadai.circuit_geometry import preview_geometry
from cadai.circuit_spec_plan import preview_circuit
from cadai.circuit_spec_schema import SPEC_VERSION, digest
from cadai.template_catalog import TemplateCatalog
from cadai.template_placement import plan_layout, preview_placement


def fixture():
    spec = {
        "schema": SPEC_VERSION,
        "project_ref": "p:1",
        "binding_snapshot": "s:1",
        "kind": "circuit",
        "target": {"library": "w", "cell": "c", "view": "schematic"},
        "instances": [
            {"id": "R0", "name": "R0", "master": "m0", "role": "device", "parameters": {},
             "connections": {"PLUS": "IN", "MINUS": "MID"}, "unconnected": {}},
            {"id": "R1", "name": "R1", "master": "m0", "role": "device", "parameters": {},
             "connections": {"PLUS": "VSS", "MINUS": "MID"}, "unconnected": {}},
            {"id": "R2", "name": "R2", "master": "m0", "role": "device", "parameters": {},
             "connections": {"PLUS": "MID", "MINUS": "OUT"}, "unconnected": {}},
        ],
        "nets": [
            {"name": name, "scope": "local"}
            for name in ("IN", "MID", "OUT", "VSS")
        ],
        "ports": [
            {"name": "IN", "net": "IN", "direction": "input"},
            {"name": "OUT", "net": "OUT", "direction": "output"},
            {"name": "VSS", "net": "VSS", "direction": "inputOutput"},
        ],
    }
    masters = [
        {
            "id": "m0",
            "kind": "device",
            "target": {"library": "smic28", "cell": "res", "view": "symbol"},
            "library_path": "/pdk",
            "revision": "rev1",
            "terminals": [
                {"name": "PLUS", "direction": "inputOutput"},
                {"name": "MINUS", "direction": "inputOutput"},
            ],
            "terminals_complete": True,
            "parameters_complete": True,
            "parameters": [{"name": "r", "type": "string", "editable": True}],
            "callbacks": {"status": "none"},
        }
    ]
    bindings = {
        "schema": "cad.circuit.bindings.v1",
        "project_ref": "p:1",
        "snapshot_ref": "s:1",
        "source_ref": "fixture",
        "evidence_kind": "test_fixture",
        "masters": masters,
    }
    geometry = {
        "schema": "cad.circuit.geometry.v1",
        "project_ref": "p:1",
        "binding_snapshot": "s:1",
        "binding_digest": preview_circuit(spec, bindings)["plan"]["binding_digest"],
        "source_ref": "fixture",
        "evidence_kind": "test_fixture",
        "coordinate_system": "master_local_schematic_uu",
        "instances": [
            {
                "instance": instance["id"],
                "master_revision": "rev1",
                "parameters_digest": digest(instance["parameters"]),
                "occupied_bbox": [[-0.125, -0.125], [0.125, 0.125]],
                "terminals": [
                    {"name": "PLUS",
                     "anchors": [{"id": "p0", "xy": [-0.125, 0.0], "escape": "left"}]},
                    {"name": "MINUS",
                     "anchors": [{"id": "p0", "xy": [0.125, 0.0], "escape": "right"}]},
                ],
            }
            for instance in spec["instances"]
        ],
    }
    return spec, bindings, geometry


def relation_view():
    box = [[-16, -8], [16, 8]]
    return {
        "schema": "cad.template.relations.v1",
        "template_ref": "tpl_" + "0" * 64,
        "unit": "source_dbu",
        "instances": [
            {"device": "d0", "name": "R0", "kind": "resistor", "xy": [0, 0],
             "orient": "R0", "bbox": box},
            {"device": "d1", "name": "R1", "kind": "resistor", "xy": [240, 0],
             "orient": "MY", "bbox": box},
            {"device": "d2", "name": "R2", "kind": "resistor", "xy": [0, 240],
             "orient": "R90", "bbox": box},
        ],
        "rows": [{"y": 0, "members": ["R0", "R1"]}],
        "columns": [],
        "mirrored_pairs": [
            {"a": "R0", "b": "R1", "axis": "vertical", "gap": 40, "same_kind": True,
             "shared_nets": []}
        ],
        "alignment": [],
        "groups": [],
        "dbu_per_uu": 160,
        "spacing": {"nearest": {}, "classes": {"tight": 40, "medium": 80, "members": {}}},
        "gaps": [],
    }


DEVICE_MAP = {"d0": "R0", "d1": "R1", "d2": "R2"}


def boxes_of(layout, geometry):
    result = {}
    rows = {row["instance"]: row for row in geometry["instances"]}
    for key, position in layout["instances"].items():
        box = rows[key]["occupied_bbox"]
        mirrored = position["orientation"]
        left, right = box[0][0], box[1][0]
        bottom, top = box[0][1], box[1][1]
        if mirrored == "MY":
            left, right = -right, -left
        result[key] = [
            [position["xy"][0] + left, position["xy"][1] + bottom],
            [position["xy"][0] + right, position["xy"][1] + top],
        ]
    return result


def test_rows_pairs_and_ports_follow_the_relations():
    spec, bindings, geometry = fixture()
    layout, report = plan_layout(spec, bindings, geometry, relation_view(), DEVICE_MAP)

    boxes = boxes_of(layout, geometry)
    assert boxes["R0"][0][1] == boxes["R1"][0][1]  # same row: shared bottom edge
    assert boxes["R0"][1][0] < boxes["R1"][0][0]  # left-to-right order kept
    assert layout["instances"]["R1"]["orientation"] == "MY"  # mirrored partner
    assert report["mirrored_pairs"] == [
        {"a": "R0", "b": "R1", "a_orientation": "R0", "b_orientation": "MY"}
    ]
    # The singleton keeps its source row above the pair and is reported.
    assert not any(g.get("instance") == "R2" for g in report["gaps"])
    assert boxes["R2"][0][1] > boxes["R0"][1][1]
    # the template's own orientation (here R90) is reused for the leftover instance
    assert layout["instances"]["R2"]["orientation"] == "R90"
    # ports: VSS is a ground name -> bottom band; OUT on the right
    assert report["ports"]["right"] == ["OUT"]
    assert report["ports"]["left"] == ["IN", "VSS"]
    vss_y = layout["ports"]["VSS"]["xy"][1]
    in_y = layout["ports"]["IN"]["xy"][1]
    assert vss_y < in_y
    # Stubs leave the pin toward the circuit: left band rightwards, right band leftwards.
    assert layout["ports"]["IN"]["escape"] == "right"
    assert layout["ports"]["VSS"]["escape"] == "right"
    assert layout["ports"]["OUT"]["escape"] == "left"
    for port in layout["ports"].values():
        assert port["xy"][0] / layout["grid"] == round(port["xy"][0] / layout["grid"])
        assert port["xy"][1] / layout["grid"] == round(port["xy"][1] / layout["grid"])
    assert all(
        layout["ports"][name]["xy"][0] < min(box[0][0] for box in boxes.values())
        for name in ("IN", "VSS")
    )
    gap = min(
        abs(boxes["R0"][1][0] - boxes["R1"][0][0]), abs(boxes["R1"][1][0] - boxes["R0"][0][0])
    )
    assert abs(gap - report["spacing"]["column_gap"]) < 1e-9
    assert report["spacing"]["source_column_pitch"] == 240


def test_ports_sit_on_the_terminal_row_they_drive():
    """A band slot is only the fallback: a port that drives a row shares its pin line."""
    spec, bindings, geometry = fixture()
    reference = relation_view()
    reference["instances"][2]["xy"][1] = -240  # IN drives the upper pair in this case.
    layout, report = plan_layout(spec, bindings, geometry, reference, DEVICE_MAP)
    rows = {row["port"]: row for row in report["port_rows"]}
    assert rows["IN"]["source"] == "terminal_row"
    assert rows["IN"]["terminal"] == "R0.PLUS"
    assert layout["ports"]["IN"]["xy"][1] == layout["instances"]["R0"]["xy"][1]
    assert rows["IN"]["band_slot"] == 0.0 and rows["IN"]["y"] < rows["IN"]["band_slot"]
    # VSS keeps its band slot: a labelled stub does not need the far row.
    assert rows["VSS"]["source"] == "band_slot" and rows["VSS"]["terminal"] is None
    assert layout["ports"]["VSS"]["xy"][1] == rows["VSS"]["band_slot"]
    assert layout["ports"]["VSS"]["xy"][1] < layout["ports"]["IN"]["xy"][1]
    preview = preview_circuit(spec, bindings)
    plan = preview_geometry(
        spec=spec,
        bindings=bindings,
        preview_digest=preview["preview_digest"],
        geometry=geometry,
        layout={**layout, "routing": {"mode": "end_to_end"}},
    )["plan"]
    assert plan["geometry_checks_passed"], plan["issues"]
    assert plan["layout_review"] == []


def test_placement_is_deterministic_and_validates_downstream():
    spec, bindings, geometry = fixture()
    first = plan_layout(spec, bindings, geometry, relation_view(), DEVICE_MAP)[0]
    second = plan_layout(spec, bindings, geometry, relation_view(), DEVICE_MAP)[0]
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    preview = preview_circuit(spec, bindings)
    plan = preview_geometry(
        spec=spec,
        bindings=bindings,
        preview_digest=preview["preview_digest"],
        geometry=geometry,
        layout={**first, "routing": {"mode": "end_to_end", "max_bends": 4}},
    )["plan"]
    assert plan["geometry_checks_passed"], plan["issues"]
    assert plan["routing"]["mode"] == "end_to_end"
    assert plan["routing"]["metrics"]["terminals"] == 3
    assert plan["routing"]["port_stub_terminals"] == 3


def test_singleton_rows_follow_source_height_instead_of_instance_name():
    spec, bindings, geometry = fixture()
    reference = relation_view()
    reference["rows"] = []
    reference["mirrored_pairs"] = []
    for row, y in zip(reference["instances"], (0, 320, 160)):
        row["xy"][1] = y
    layout, report = plan_layout(spec, bindings, geometry, reference, DEVICE_MAP)
    assert [row["members"] for row in report["rows"]] == [["R1"], ["R2"], ["R0"]]
    assert (layout["instances"]["R1"]["xy"][1] > layout["instances"]["R2"]["xy"][1]
            > layout["instances"]["R0"]["xy"][1])


def test_annotation_proxy_spacing_preserves_the_requested_gap_under_mirroring():
    from cadai.circuit_geometry_schema import transform_box

    spec, bindings, geometry = fixture()
    for row in geometry["instances"]:
        row["annotation_bbox"] = [[-0.5, -0.25], [1.0, 0.75]]
    layout, report = plan_layout(spec, bindings, geometry, relation_view(), DEVICE_MAP)
    boxes = {g["instance"]: transform_box(g["annotation_bbox"],
             layout["instances"][g["instance"]]["orientation"],
             layout["instances"][g["instance"]]["xy"]) for g in geometry["instances"]}
    assert boxes["R1"][0][0] - boxes["R0"][1][0] == report["spacing"]["column_gap"]
    assert boxes["R2"][0][1] > max(boxes[k][1][1] for k in ("R0", "R1"))


def test_planned_port_stubs_stay_clear_of_the_body():
    """Port stubs run toward the circuit, so the gap always fits the stub."""
    spec, bindings, geometry = fixture()
    layout, _ = plan_layout(
        spec,
        bindings,
        geometry,
        relation_view(),
        DEVICE_MAP,
        {"port_gap": 0.25, "stub_length": 1.0},
    )
    boxes = boxes_of(layout, geometry)
    left_edge = min(box[0][0] for box in boxes.values())
    right_edge = max(box[1][0] for box in boxes.values())
    assert left_edge - layout["ports"]["IN"]["xy"][0] >= 1.0 + layout["grid"]
    assert layout["ports"]["OUT"]["xy"][0] - right_edge >= 1.0 + layout["grid"]
    preview = preview_circuit(spec, bindings)
    plan = preview_geometry(
        spec=spec,
        bindings=bindings,
        preview_digest=preview["preview_digest"],
        geometry=geometry,
        layout={**layout, "routing": {"mode": "end_to_end", "max_bends": 4}},
    )["plan"]
    assert plan["geometry_checks_passed"], plan["issues"]


def test_placement_uses_explicit_port_roles():
    spec, bindings, geometry = fixture()
    layout, report = plan_layout(
        spec,
        bindings,
        geometry,
        relation_view(),
        DEVICE_MAP,
        {"port_roles": {"IN": "power", "VSS": "ground"}},
    )
    assert report["ports"]["left"] == ["IN", "VSS"]
    assert layout["ports"]["IN"]["role"] == "power"
    assert layout["ports"]["IN"]["xy"][1] > layout["ports"]["VSS"]["xy"][1]


def capture_rows():
    rows = [
        {
            "kind": "header",
            "schema_version": 1,
            "status": "raw_capture",
            "lib": "demo",
            "cell": "DIV",
            "view": "schematic",
            "library_path": "/reference/demo",
            "tool_version": "test",
            "dbu_per_uu": 160,
            "bbox": [[0, 0], [40, 40]],
            "net_global_semantics": "uniform_boolean_v1",
        }
    ]
    for index, (name, x) in enumerate((("M0", 0), ("M1", 100), ("M2", 0))):
        rows.append(
            {
                "kind": "instance",
                "name": name,
                "libName": "gpdk045",
                "cellName": "resnsnpoly",
                "viewName": "symbol",
                "xy": [x, 0 if name != "M2" else 150],
                "orient": "MY" if name == "M1" else "R0",
                "bbox": [[x - 20, -10], [x + 20, 10]],
                "master_available": True,
            }
        )
        rows.extend(
            {"kind": "instance_terminal", "instance": name, "name": pin, "net": net}
            for pin, net in (("PLUS", "N1"), ("MINUS", "N2"))
        )
    rows.extend(
        {
            "kind": "net",
            "name": net,
            "numBits": 1,
            "sigType": "signal",
            "is_global": False,
            "instance_terminal_count": 3,
        }
        for net in ("N1", "N2")
    )
    rows.append(
        {"kind": "terminal", "name": "VIN", "direction": "input", "numBits": 1, "net": "N1"}
    )
    rows.append({"kind": "pin", "terminal": "VIN", "net": "N1"})
    counts = Counter(row["kind"] for row in rows)
    rows.append(
        {
            "kind": "footer",
            "complete": True,
            "counts": {
                key: counts[key]
                for key in ("instance", "instance_terminal", "net", "terminal", "pin", "shape")
            },
        }
    )
    return rows


def test_tool_end_to_end_from_a_stored_template(tmp_path, monkeypatch):
    monkeypatch.delenv("SICO_AI_TEMPLATE_ROOT", raising=False)
    from sicostate import project_directory

    root = project_directory(tmp_path, "ai/reference/circuit-templates", create=True)
    make_catalog(root, capture_rows())
    catalog = TemplateCatalog(root)
    record = catalog.get(catalog.query({"offset": 0, "limit": 1})["templates"][0]["template_ref"])
    device_ids = [row["id"] for row in record["topology"]["devices"]]
    assert len(device_ids) == 3
    spec, bindings, geometry = fixture()
    targets = ["R0", "R1", "R2"]
    args = {
        "template_ref": record["template_ref"],
        "spec": spec,
        "bindings": bindings,
        "geometry": geometry,
        "template_use": {
            "schema": "cad.circuit.template-use.v1",
            "template_ref": record["template_ref"],
            "devices": [
                {"source_device": source, "instance": target, "master": "m0",
                 "terminal_map": {"PLUS": "PLUS", "MINUS": "MINUS"}}
                for source, target in zip(device_ids, targets)
            ],
            "net_map": {},
            "port_map": {},
        },
    }
    result = preview_placement(args, workspace=tmp_path)
    assert result["ok"] and result["status"] == "placement_planned"
    assert set(result["layout"]["instances"]) == set(targets)
    assert result["report"]["rows"]
    assert result["placement_digest"] == digest(result["layout"])
    assert result["template"]["cell"] == "DIV"
    with pytest.raises(Exception):
        preview_placement({**args, "template_use": None, "device_map": None}, workspace=tmp_path)


def test_spacing_follows_the_template_pitch_in_target_units():
    spec, bindings, geometry = fixture()
    view = relation_view()
    view["instances"][1]["xy"] = [240, 0]  # 240 dbu = 1.5 uu at 160 dbu/uu
    layout, report = plan_layout(spec, bindings, geometry, view, DEVICE_MAP)
    bodies = boxes_of(layout, geometry)
    gap = bodies["R1"][0][0] - bodies["R0"][1][0]
    assert report["spacing"]["dbu_per_uu"] == 160
    assert report["spacing"]["source_column_pitch"] == 240
    # 1.5 uu pitch minus the 0.25 uu body width -> 1.25 uu gap
    assert abs(gap - 1.25) < 1e-9


def test_loose_spacing_is_the_default_and_compact_is_optional():
    spec, bindings, geometry = fixture()
    tight = relation_view()
    tight["instances"][1]["xy"] = [40, 0]  # pitch below the body width
    layout, report = plan_layout(spec, bindings, geometry, tight, DEVICE_MAP)
    assert report["spacing"]["mode"] == "loose"
    bodies = boxes_of(layout, geometry)
    gap = bodies["R1"][0][0] - bodies["R0"][1][0]
    assert abs(gap - 1.0) < 1e-9  # generous floor, never a cramped channel
    compact, compact_report = plan_layout(
        spec, bindings, geometry, tight, DEVICE_MAP, {"spacing": "compact"}
    )
    compact_gap = (
        boxes_of(compact, geometry)["R1"][0][0] - boxes_of(compact, geometry)["R0"][1][0]
    )
    assert compact_report["spacing"]["mode"] == "compact"
    assert compact_gap < gap


def test_geometry_review_flags_centerline_pins():
    spec, bindings, geometry = fixture()
    vertical = json.loads(json.dumps(geometry))
    for row in vertical["instances"]:
        row["occupied_bbox"] = [[-0.125, -0.5], [0.125, 0.5]]
        for terminal in row["terminals"]:
            for anchor in terminal["anchors"]:
                anchor["xy"] = [0.0, 0.4 if terminal["name"] == "PLUS" else -0.4]
    _, report = plan_layout(spec, bindings, vertical, relation_view(), DEVICE_MAP)
    assert report["geometry_review"], report["geometry_review"]
    assert all(
        "pins_on_vertical_centerline; use up/down escapes" in item["warnings"]
        for item in report["geometry_review"]
    )
