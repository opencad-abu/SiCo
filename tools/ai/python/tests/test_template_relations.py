"""Derived reference views: placement relations and routing style (offline)."""

from __future__ import annotations

import pytest

from cadai.template_relations import (
    MIRROR_VERTICAL,
    relations,
    routing_style,
)
from cadai.template_schema import TemplateUnavailable


def instances():
    return [
        {
            "device": "d0",
            "source_name": "M0",
            "relative_xy": [0, 0],
            "orient": "R0",
            "bbox": [[-10, -10], [10, 10]],
        },
        {
            "device": "d1",
            "source_name": "M1",
            "relative_xy": [100, 0],
            "orient": "MY",
            "bbox": [[90, -10], [110, 10]],
        },
        {
            "device": "d2",
            "source_name": "R0",
            "relative_xy": [0, 100],
            "orient": "R0",
            "bbox": [[-10, 90], [10, 110]],
        },
        {
            "device": "d3",
            "source_name": "R1",
            "relative_xy": [100, 100],
            "orient": "R0",
            "bbox": [[90, 90], [110, 110]],
        },
    ]


def shapes():
    return [
        {
            "kind": "shape",
            "net": "VSS",
            "figure": {
                "objType": "line",
                "points": [[0, 0], [0, 50]],
                "lpp": ["wire", "drawing"],
            },
        },
        {
            "kind": "shape",
            "net": "VSS",
            "figure": {
                "objType": "line",
                "points": [[0, 50], [50, 50]],
                "lpp": ["wire", "drawing"],
            },
        },
        {
            "kind": "shape",
            "net": "VSS",
            "figure": {
                "objType": "line",
                "points": [[50, 50], [50, 0]],
                "lpp": ["wire", "drawing"],
            },
        },
        {
            "kind": "shape",
            "net": "VSS",
            "figure": {
                "objType": "ellipse",
                "ellipseBBox": [[48, 48], [52, 52]],
                "lpp": ["wire", "drawing"],
            },
        },
        {
            "kind": "shape",
            "net": "VSS",
            "figure": {
                "objType": "label",
                "bBox": [[0, 0], [20, 10]],
                "text": "VSS",
                "lpp": ["wire", "label"],
            },
        },
    ]


def record(asset=None, topology=None):
    return {
        "template_ref": "tpl_" + "0" * 64,
        "assets": {
            "schematic": asset
            if asset is not None
            else {
                "coordinate_space": "source_dbu_integer_relative",
                "dbu_per_uu": 160,
                "instances": instances(),
                "shapes": shapes(),
            }
        },
        "topology": topology
        if topology is not None
        else {
            "devices": [
                {
                    "id": "d0",
                    "kind": "nmos",
                    "pins": [{"name": "S", "net": "VSS"}, {"name": "B", "net": "VSS"}],
                },
                {
                    "id": "d1",
                    "kind": "nmos",
                    "pins": [{"name": "S", "net": "VSS"}, {"name": "B", "net": "VSS"}],
                },
                {"id": "d2", "kind": "resistor", "pins": [{"name": "PLUS", "net": "n1"}]},
                {"id": "d3", "kind": "resistor", "pins": [{"name": "PLUS", "net": "n1"}]},
            ]
        },
    }


def test_relations_rows_columns_and_mirrored_pairs():
    result = relations(record())
    assert result["schema"] == "cad.template.relations.v2"
    assert result["unit"] == "source_dbu"
    assert {row["y"] for row in result["rows"]} == {0, 100}
    assert {row["x"] for row in result["columns"]} == {0, 100}
    assert result["instances"][0]["kind"] == "nmos"
    pairs = result["mirrored_pairs"]
    assert len(pairs) == 1
    assert (pairs[0]["a"], pairs[0]["b"]) == ("M0", "M1")
    assert pairs[0]["gap"] == 100 and pairs[0]["same_kind"] is True
    assert pairs[0]["shared_nets"] == ["VSS"]
    assert {"kind": "mirrored_pair", "members": ["M0", "M1"],
            "evidence": "source_geometry", "strength": "preference"} in result["groups"]
    assert all(g["code"] == "source_terminal_geometry_missing" for g in result["gaps"])
    assert result["spacing"]["classes"] is not None
    assert "recompute_with_target_master_geometry" in result["use"]


def test_relations_require_schematic_asset():
    with pytest.raises(TemplateUnavailable):
        relations({"template_ref": "tpl_x", "assets": {}, "topology": {}})


def test_relations_legacy_origin_is_subtracted():
    legacy = {
        "dbu_per_uu": 160,
        "origin": [100, 100],
        "instances": [
            {"device": "d0", "source_name": "M0", "xy": [100, 100], "orient": "R0",
             "bbox": [[90, 90], [110, 110]]},
            {"device": "d1", "source_name": "M1", "xy": [200, 100], "orient": "MY",
             "bbox": [[190, 90], [210, 110]]},
        ],
        "shapes": [],
    }
    result = relations(record(asset=legacy))
    assert result["unit"] == "source_user_units"
    positions = {row["name"]: row["xy"] for row in result["instances"]}
    assert positions == {"M0": [0, 0], "M1": [100, 0]}
    assert len(result["mirrored_pairs"]) == 1


def test_relations_report_missing_geometry_without_failing():
    asset = {
        "coordinate_space": "source_dbu_integer_relative",
        "dbu_per_uu": 160,
        "instances": [
            {"device": "d0", "source_name": "M0", "relative_xy": [0, 0], "orient": "R0"},
            {"device": "d1", "source_name": "M1", "relative_xy": [100, 0], "orient": "MY",
             "bbox": [[90, -10], [110, 10]]},
        ],
        "shapes": [],
    }
    result = relations(record(asset=asset))
    assert {"code": "bounding_box_missing", "device": "d0"} in result["gaps"]
    assert result["rows"] == [{"y": 0, "members": ["M0", "M1"]}]
    assert [row["name"] for row in result["instances"]] == ["M0", "M1"]


def test_mirror_table_covers_every_orientation_pair():
    for orientation, mirror in MIRROR_VERTICAL.items():
        assert MIRROR_VERTICAL[mirror] == orientation
        assert mirror != orientation


def test_mirrored_pair_requires_row_adjacency():
    asset = {
        "coordinate_space": "source_dbu_integer_relative",
        "dbu_per_uu": 160,
        "instances": [
            {"device": "d0", "source_name": "M0", "relative_xy": [0, 0], "orient": "R0",
             "bbox": [[-10, -10], [10, 10]]},
            {"device": "d1", "source_name": "M1", "relative_xy": [100, 0], "orient": "MY",
             "bbox": [[90, -10], [110, 10]]},
            {"device": "d2", "source_name": "M2", "relative_xy": [400, 0], "orient": "MY",
             "bbox": [[390, -10], [410, 10]]},
        ],
        "shapes": [],
    }
    result = relations(record(asset=asset))
    pairs = {(pair["a"], pair["b"]) for pair in result["mirrored_pairs"]}
    assert pairs == {("M0", "M1")}


def test_routing_style_counts_channels_branches_and_labels():
    result = routing_style(record())
    assert result["schema"] == "cad.template.routing_style.v1"
    assert result["totals"] == {
        "segments": 3,
        "junctions": 1,
        "labels": 1,
        "terminals": 6,
        "orthogonal_segments": 3,
    }
    assert result["points_per_segment"] == {"2": 3}
    net = result["nets"][0]
    assert net["net"] == "VSS"
    assert net["segments"] == 3 and net["junctions"] == 1 and net["labels"] == 1
    assert net["rows"] == [50] and net["columns"] == [0, 50]
    assert net["branch_degree_max"] == 2
    assert net["terminals"] == 4
    assert result["attachment"] == {"on_instance_bbox": 1, "on_junction": 2, "other": 3}
    assert "target wires are recomputed" in result["use"]
