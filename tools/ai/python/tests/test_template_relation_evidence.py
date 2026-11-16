"""Relative geometry is evidence, not a functional group or a complete truncated list."""

import copy

import pytest
from sicostate import project_directory
from test_template_relations import record

from cadai.template_relations import relations
from cadai.template_schema import TemplateUnavailable


def test_mirroring_only_claims_geometric_group_evidence():
    source = record()
    source["topology"]["devices"][0]["kind"] = "unknown"
    source["topology"]["devices"][1]["kind"] = "unknown"
    result = relations(source)
    assert result["schema"] == "cad.template.relations.v2"
    assert result["mirrored_pairs"][0]["same_kind"] is False
    assert result["groups"][0] == {
        "kind": "mirrored_pair", "members": ["M0", "M1"],
        "evidence": "source_geometry", "strength": "preference",
    }
    assert "functional" in result["use"]


@pytest.mark.parametrize("box", [None, [[1, 1], [0, 0]], [[0], [1]], "unobserved"])
def test_missing_or_invalid_box_does_not_erase_valid_position_relations(box):
    source = record()
    source["assets"]["schematic"]["instances"][0]["bbox"] = box
    result = relations(source)
    assert result["instances"][0]["bbox"] is None
    assert {"y": 0, "members": ["M0", "M1"]} in result["rows"]
    assert any(g["code"] == "bounding_box_missing" for g in result["gaps"])
    assert all("M0" not in (e["a"], e["b"]) for e in result["alignment"])


def test_legacy_frame_normalizes_position_and_box_together():
    source = record()
    asset = source["assets"]["schematic"]
    asset.pop("coordinate_space")
    asset["origin"] = [100, 200]
    row = asset["instances"][0]
    row.pop("relative_xy")
    row.update(xy=[100, 200], bbox=[[90, 190], [110, 210]])
    assert relations(source)["instances"][0]["bbox"] == [[-10, -10], [10, 10]]


def test_missing_topology_placement_and_orientation_remain_visible():
    source = record()
    rows = source["assets"]["schematic"]["instances"]
    rows.pop()
    rows[0]["orient"] = "unknown"
    result = relations(source)
    assert dict(code="placement_incomplete", device="d3") in result["gaps"]
    assert dict(code="source_orientation_unavailable", device="d0") in result["gaps"]


@pytest.mark.parametrize("field,value", [("device", "d0"), ("source_name", "M0")])
def test_ambiguous_placement_identity_is_not_overwritten(field, value):
    source = record()
    source["assets"]["schematic"]["instances"][1][field] = value
    with pytest.raises(TemplateUnavailable, match="duplicate"):
        relations(source)


def many_rows(count):
    source = record()
    source["assets"]["schematic"]["instances"] = [
        dict(device=f"d{i}", source_name=f"M{i}", relative_xy=[0, i * 100],
             orient="R0", bbox=[[-10, i * 100 - 10], [10, i * 100 + 10]])
        for i in range(count)
    ]
    source["topology"]["devices"] = [dict(id=f"d{i}", kind="resistor", pins=[])
                                       for i in range(count)]
    return source


def test_alignment_beyond_32_devices_is_bounded_and_counted():
    result = relations(many_rows(40))
    assert len(result["alignment"]) == 64
    assert result["coverage"]["alignment"] == dict(total=780, returned=64, omitted=716)
    assert any(g["code"] == "relation_list_truncated" and g["relation"] == "alignment"
               for g in result["gaps"])


def test_group_budget_reports_all_candidates_deterministically():
    source = many_rows(72)
    for index, row in enumerate(source["assets"]["schematic"]["instances"]):
        row["relative_xy"] = [100 * (index % 2), (index // 2) * 100]
        row["orient"] = "R0" if index % 2 == 0 else "MY"
    result = relations(source)
    assert len(result["groups"]) == 32
    assert result["coverage"]["groups"] == dict(total=36, returned=32, omitted=4)
    source["assets"]["schematic"]["instances"].reverse()
    assert relations(source) == result


def test_relations_reject_work_budget_before_quadratic_derivation():
    with pytest.raises(TemplateUnavailable, match="relations.*budget"):
        relations(many_rows(257))


def test_current_relations_do_not_change_legacy_projection_content():
    from test_template_project import base_record

    from cadai.template_project import project_record
    from cadai.template_relations_legacy import relations as legacy_relations

    source = base_record()
    before = copy.deepcopy(source)
    legacy = legacy_relations(source)
    current = relations(source)
    assert legacy["schema"] == "cad.template.relations.v1"
    assert current["schema"] == "cad.template.relations.v2"
    assert project_record(source, "L1")["relations"]["schema"] == legacy["schema"]
    assert source == before


def test_terminal_axis_ties_and_missing_anchors_are_visible_without_guessing():
    source = record()
    asset = source["assets"]["schematic"]
    asset["wire_reference"] = {"geometry": [dict(device="d0", terminals=[
        dict(name=str(i), anchors=[dict(xy=p)]) for i, p in enumerate(
            ([0, 0], [0, 40], [40, 0], [40, 40]))])]}
    asset["wire_reference"]["geometry"][0]["terminals"].append(dict(name="X", anchors=[]))
    result = relations(source)
    assert result["device_axes"] == []
    gaps = result["gaps"]
    assert {g["axis"] for g in gaps if g["code"] == "source_terminal_axis_ambiguous"} == {"x", "y"}
    assert any(g["code"] == "source_terminal_anchor_unavailable" and g["terminal"] == "X"
               for g in gaps)


@pytest.mark.parametrize("scale", [None, 0, -1, float("inf"), True])
def test_unknown_scale_is_a_gap_and_cannot_be_assumed_as_user_units(scale):
    from test_template_placement import DEVICE_MAP, fixture

    from cadai.circuit_spec_schema import CircuitSpecError
    from cadai.template_placement_layout import plan_layout

    source = record()
    source["assets"]["schematic"]["dbu_per_uu"] = scale
    view = relations(source)
    assert dict(code="coordinate_scale_missing") in view["gaps"]
    with pytest.raises(CircuitSpecError, match="coordinate scale missing"):
        plan_layout(*fixture(), view, DEVICE_MAP)


def test_public_relations_paging_reports_truncation_beyond_the_page(tmp_path):
    from test_template_project import base_record

    from cadai.template_build import publish, summary
    from cadai.template_catalog import call_template

    source = base_record()
    large = many_rows(40)
    source["assets"]["schematic"] = large["assets"]["schematic"]
    source["topology"]["devices"] = large["topology"]["devices"]
    source["summary"] = summary(source)
    publish([source], project_directory(tmp_path, "ai/reference/circuit-templates/catalogs", create=True) / "source.sqlite3")
    result = call_template("get_circuit_template", {
        "template_ref": source["template_ref"], "section": "relations",
        "entity": "alignment", "limit": 2,
    }, workspace=tmp_path)
    assert result["returned"] == 2 and result["total"] == 64
    assert result["metadata"]["coverage"]["alignment"]["omitted"] == 716


@pytest.mark.parametrize("level,expected", [
    ("L1", "80b14a368c38ac6de8b7880511a3f505c205e02532e426dfd664858ee2cf9bf9"),
    ("L2", "36c29e748ddb0062d62919c544da37c7d0dcf7bad81ee5bf84b6b885310dbbf8"),
    ("L3", "9faa6fec1d9b07c65ddc1f51c9d263d95c376292f0d56aecb1a6005d370f986f"),
])
def test_frozen_projection_reconstruction_matches_pre_audit_content(level, expected):
    from test_template_project import base_record

    from cadai.template_project import project_record
    from cadai.template_schema import digest

    # Fixed full-record hashes generated with 2207beb's relation/projection implementation.
    assert digest(project_record(base_record(), level)) == expected
