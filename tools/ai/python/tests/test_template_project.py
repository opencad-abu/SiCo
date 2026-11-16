"""Sanitized L1/L2/L3 projections of one stored template record."""

from __future__ import annotations

import json

import pytest

from cadai.template_project import LEVELS, project_record, projection_summary
from cadai.template_schema import TemplateError

IDENTITY_SECRETS = (
    "M0",
    "M1",
    "TOP_CELL_NAME",
    "/private/library/path",
    "value_skill",
    "500n",
    "1.25u",
)
PDK_IDENTITIES = ("nmos2v", "resnsnpoly")


def base_record():
    return {
        "schema_version": "cad.circuit.template.v2",
        "rule_version": "20260915.2",
        "coordinate_space": "source_dbu_integer_relative",
        "template_ref": "tpl_" + "a" * 64,
        "capture_sha256": "b" * 64,
        "category": "analog",
        "pin_policy": {"input": "lower_left", "inputOutput": "upper_left", "output": "right"},
        "missing_assets": [],
        "provenance": {"origin": "capture", "note": "kept in base provenance"},
        "source": {
            "lib": "amsLDO",
            "cell": "LDO_MASTER",
            "view": "schematic",
            "library_path": "/private/library/path/amsLDO",
            "working_directory": "/private/work",
            "forced_cds_lib": "/private/work/cds.lib",
            "tool_version": "IC23.1",
            "dbu_per_uu": 160,
        },
        "topology": {
            "level": "direct",
            "devices": [
                {
                    "id": "d0",
                    "source_name": "M0",
                    "kind": "nmos",
                    "master": {"library": "gpdk045", "cell": "nmos2v", "view": "symbol"},
                    "pins": [{"name": "D", "net": "n1"}, {"name": "G", "net": "n0"}],
                },
                {
                    "id": "d1",
                    "source_name": "M1",
                    "kind": "resistor",
                    "master": {"library": "gpdk045", "cell": "resnsnpoly", "view": "symbol"},
                    "pins": [{"name": "PLUS", "net": "n0"}],
                },
            ],
            "nets": [
                {"id": "n0", "source_name": "VDD", "is_global": False, "num_bits": 1},
                {"id": "n1", "source_name": "N3", "is_global": False, "num_bits": 1},
            ],
            "ports": [{"id": "p0", "name": "VDD", "direction": "input", "net": "n0"}],
            "counts": {"devices": 2, "nets": 2, "ports": 1},
            "gaps": [],
        },
        "assets": {
            "schematic": {
                "coordinate_space": "source_dbu_integer_relative",
                "dbu_per_uu": 160,
                "source": {
                    "lib": "amsLDO",
                    "cell": "LDO_MASTER",
                    "view": "schematic",
                    "library_path": "/private/library/path/amsLDO",
                },
                "instances": [
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
                ],
                "shapes": [
                    {
                        "kind": "shape",
                        "net": "VDD",
                        "figure": {"objType": "line", "points": [[0, 0], [0, 50]]},
                    },
                    {
                        "kind": "shape",
                        "net": "VDD",
                        "figure": {"objType": "ellipse", "ellipseBBox": [[-2, 48], [2, 52]]},
                    },
                    {
                        "kind": "shape",
                        "net": "VDD",
                        "figure": {"objType": "label", "bBox": [[0, 0], [20, 10]], "text": "VDD"},
                    },
                ],
                "properties": [
                    {"instance": "M0", "name": "w", "type": "string", "value_skill": '"500n"'},
                    {
                        "instance": "M0",
                        "name": "fingers",
                        "type": "string",
                        "value_skill": '"2"',
                    },
                    {
                        "instance": "M0",
                        "name": "ad",
                        "type": "string",
                        "value_skill": '"1.25u"',
                    },
                    {"instance": "M1", "name": "r", "type": "string", "value_skill": '"25.6k"'},
                    {
                        "instance": "M1",
                        "name": "segments",
                        "type": "string",
                        "value_skill": '"3"',
                    },
                ],
            },
            "symbol": {
                "source": {"lib": "amsLDO", "cell": "LDO_MASTER", "view": "symbol"},
                "instance_properties": [
                    {"instance": "M0", "name": "w", "value_skill": '"500n"'}
                ],
                "pins": [{"name": "VDD"}],
            },
        },
    }


def text_of(record):
    return json.dumps(record, ensure_ascii=False, sort_keys=True)


def test_levels_are_closed_set():
    assert LEVELS == ("L1", "L2", "L3")
    with pytest.raises(TemplateError):
        project_record(base_record(), "L4")


def test_l1_keeps_relations_and_style_without_geometry_or_identity():
    projected = project_record(base_record(), "L1")
    assert projected["detail_level"] == "L1"
    assert projected["assets"] == {}
    assert projected["relations"]["schema"] == "cad.template.relations.v1"
    assert projected["routing_style"]["schema"] == "cad.template.routing_style.v1"
    text = text_of(projected)
    for secret in IDENTITY_SECRETS + PDK_IDENTITIES:
        assert secret not in text
    devices = projected["topology"]["devices"]
    assert [device["id"] for device in devices] == ["d0", "d1"]
    assert all("master" not in device and "source_name" not in device for device in devices)
    # interface ports keep their names, internal nets are placeholder ids
    assert projected["topology"]["nets"][0]["source_name"] == "VDD"
    assert projected["topology"]["nets"][1]["source_name"].startswith("n")
    relation_rows = projected["relations"]["instances"]
    assert all("xy" not in row and "bbox" not in row for row in relation_rows)
    net = projected["routing_style"]["nets"][0]
    assert "rows" not in net and "columns" not in net
    # the fixture wire is vertical: one channel column, no horizontal run
    assert net["row_count"] == 0 and net["column_count"] == 1


def test_l2_keeps_pdk_identity_and_geometry_but_no_values():
    projected = project_record(base_record(), "L2")
    assert projected["detail_level"] == "L2"
    assert "relations" not in projected
    text = text_of(projected)
    for secret in IDENTITY_SECRETS:
        assert secret not in text
    for identity in PDK_IDENTITIES:
        assert identity in text  # L2 keeps PDK master identity
    assert "value_skill" not in text
    assert "500n" not in text and "25.6k" not in text
    schematic = projected["assets"]["schematic"]
    assert "properties" not in schematic
    assert "instance_properties" not in projected["assets"]["symbol"]
    assert "library_path" not in text and "working_directory" not in text
    assert schematic["instances"][0]["device"] == "d0"
    assert "source_name" not in schematic["instances"][0]
    assert schematic["instances"][0]["relative_xy"] == [0, 0]
    assert schematic["shapes"][0]["net"] == "VDD"
    assert projected["topology"]["devices"][0]["master"]["cell"] == "nmos2v"


def test_l3_adds_canonical_parameters_and_reports_gaps():
    projected = project_record(base_record(), "L3")
    parameters = projected["parameters"]
    assert parameters["schema"] == "cad.template.parameters.v1"
    by_device = {row["device"]: row for row in parameters["devices"]}
    assert by_device["d0"]["parameters"] == {"w": "500n", "nf": "2"}
    # the resistor has no aliased physical parameters in this fixture
    assert by_device["d1"]["parameters"] == {"segments": "3"}
    assert {"code": "physical_parameters_missing", "device": "d1"} not in parameters["gaps"]
    text = text_of(projected)
    assert '"ad"' not in text and "1.25u" not in text
    assert "r" not in by_device["d1"]["parameters"]


@pytest.mark.parametrize("width,length", [("segW", "segL"), ("segw", "segl")])
def test_l3_accepts_resistor_dimension_spellings(width, length):
    record = base_record()
    record["assets"]["schematic"]["properties"].extend(
        [
            {"instance": "M1", "name": width, "type": "string", "value_skill": '"2u"'},
            {"instance": "M1", "name": length, "type": "string", "value_skill": '"10u"'},
        ]
    )
    projected = project_record(record, "L3")
    by_device = {row["device"]: row for row in projected["parameters"]["devices"]}
    assert by_device["d1"]["parameters"] == {
        "segw": "2u",
        "segl": "10u",
        "segments": "3",
    }


def test_physical_projection_versions_l3_without_changing_legacy_l1_l2_refs():
    # Captured from the baseline writer, independent of the current hash logic.
    legacy_refs = {
        "L1": "tpl_b3177e632d2bf252f4fa0f724659c99f6628193385d07b85edb7571861c06b63",
        "L2": "tpl_2539977359ebe7606434cd24d529ea23bc96bbf22dd600e2988cea3ba43b9ef4",
        "L3": "tpl_e84cb9756ceb298900d8a6f6750e12769d8310c9b532787f4cf81b0e76c3a507",
    }
    for level in ("L1", "L2"):
        projected = project_record(base_record(), level)
        assert projected["rule_version"] == "20260915.2"
        assert projected["template_ref"] == legacy_refs[level]
    sized = project_record(base_record(), "L3")
    assert sized["rule_version"] == "20260919.physical-projection.v1"
    assert sized["summary"]["rule_version"] == sized["rule_version"]
    assert sized["template_ref"] != legacy_refs["L3"]


def test_projections_are_deterministic_and_referenced_back():
    first = project_record(base_record(), "L1")
    second = project_record(base_record(), "L1")
    assert text_of(first) == text_of(second)
    assert first["template_ref"].startswith("tpl_")
    assert first["template_ref"] != base_record()["template_ref"]
    refs = {project_record(base_record(), level)["template_ref"] for level in LEVELS}
    assert len(refs) == 3
    assert first["provenance"]["base_template_ref"] == base_record()["template_ref"]
    assert first["capture_sha256"] == base_record()["capture_sha256"]


def test_projection_summary_is_bounded_metadata():
    summary = projection_summary(project_record(base_record(), "L3"))
    assert summary["detail_level"] == "L3"
    assert summary["has_relations"] is False
    assert summary["has_parameters"] is True
    assert summary["assets"] == ["schematic", "symbol"]
    assert "library_path" not in summary["source_keys"]
