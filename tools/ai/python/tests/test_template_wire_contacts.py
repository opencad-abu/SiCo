"""Source-only contact semantics, identity binding and conservative target checks."""

import copy

import pytest
from test_template_wire_attachment import _attachment_record, anchor, segment, v2_rows

from cadai.circuit_geometry_schema import MATRICES, transform
from cadai.template_capture_geometry import validate_geometry_capture
from cadai.template_route_paths import path_gaps
from cadai.template_schema import TemplateError, digest
from cadai.template_wire_attachment import capture_reference, compile_reference
from cadai.template_wire_contacts import (
    NET_SEMANTICS,
    VERSION,
    capture_contact_evidence,
    source_contacts,
)


def crossing_record(orientation="R0"):
    record = _attachment_record()
    asset = record["assets"]["schematic"]
    ref = asset["wire_reference"]
    ref.update(schema=VERSION, dependency_digest=digest("guard"), gaps=[])
    points = [[[0, 0], [10, 0]], [[5, -5], [5, 5]]]
    for i, line in enumerate(points):
        line = [transform(p, orientation, [7, 13]) for p in line]
        asset["shapes"][i]["figure"]["points"] = line
        asset["instances"][i].update(relative_xy=line[0], orient=orientation)
        ref["ports"][i]["xy"] = line[1]
    ref.update(capture_contact_evidence({"wire_net_semantics": NET_SEMANTICS}, asset["shapes"]))
    return record


def refresh(record):
    asset = record["assets"]["schematic"]
    asset["wire_reference"]["wire_shapes_digest"] = digest(asset["shapes"])


@pytest.mark.parametrize("orientation", list(MATRICES))
def test_guarded_isolated_crossing_preserves_separate_connected_graphs(orientation):
    record = crossing_record(orientation)
    result = compile_reference(record)
    for net, row in result["nets"].items():
        assert row["eligible"] and row["gaps"] == []
        assert len(row["edges"]) == len(row["components"]) == 1
        assert row["junctions"] == []
        crossing = row["isolated_crossings"][0]
        assert crossing["point"] == transform([5, 0], orientation, [7, 13])
        assert crossing["other_net"] != net


def test_old_reference_does_not_acquire_new_semantics_from_extra_fields():
    record = crossing_record()
    record["assets"]["schematic"]["wire_reference"]["schema"] = "cad.template.wire-reference.v1"
    for row in compile_reference(record)["nets"].values():
        assert "different_source_nets_touch" in row["gaps"]
        assert "isolated_crossings" not in row


@pytest.mark.parametrize("field,value", [
    ("wire_shapes_digest", "0" * 64), ("dependency_digest", "missing"),
    ("wire_net_semantics", "guessed"), ("wire_net_semantics", None),
])
def test_v2_requires_complete_matching_evidence(field, value):
    record = crossing_record()
    record["assets"]["schematic"]["wire_reference"][field] = value
    with pytest.raises(TemplateError, match="contact evidence"):
        compile_reference(record)


@pytest.mark.parametrize("change", ["point", "net", "label", "order"])
def test_shape_changes_invalidate_reference_digest_binding(change):
    record = crossing_record()
    shapes = record["assets"]["schematic"]["shapes"]
    if change == "point":
        shapes[0]["figure"]["points"][0][0] -= 1
    elif change == "net":
        shapes[0]["net"] = "N1"
    elif change == "label":
        shapes.append(dict(net="N0", figure=dict(objType="label", text="N0")))
    else:
        shapes.reverse()
    with pytest.raises(TemplateError, match="contact evidence"):
        compile_reference(record)


@pytest.mark.parametrize("line", [
    [[5, 0], [5, 5]], [[10, 0], [10, 5]], [[3, 0], [8, 0]], [[10, 0], [15, 0]],
])
def test_t_end_overlap_and_collinear_touch_still_fail_with_oa_names(line):
    gaps, isolated = source_contacts({"a": [segment([0, 0], [10, 0])],
                                      "b": [segment(*line, shape=1)]}, {}, observed=True)
    assert gaps["a"] == gaps["b"] == {"different_source_nets_touch"}
    assert not isolated


@pytest.mark.parametrize("obstacle", ["terminal", "third_endpoint", "marker", "other_terminal"])
def test_crossing_near_junction_or_terminal_is_not_attested(obstacle):
    lines = {"a": [segment([0, 0], [10, 0])], "b": [segment([5, -5], [5, 5], 1)]}
    anchors, markers = {}, []
    if obstacle in {"terminal", "other_terminal"}:
        anchors["a" if obstacle == "terminal" else "c"] = [anchor([5, 0])]
    elif obstacle == "third_endpoint":
        lines["a"].append(segment([5, 0], [7, 0], 2))
    else:
        markers = [[[4, -1], [6, 1]]]
    gaps, isolated = source_contacts(lines, anchors, observed=True, markers=markers)
    assert "different_source_nets_touch" in gaps["a"] & gaps["b"]
    assert not isolated


def test_terminal_on_another_net_wire_fails_without_second_wire():
    gaps, _ = source_contacts({"a": [segment([0, 0], [10, 0])]},
                             {"b": [anchor([3, 0])]}, observed=True)
    assert gaps["a"] == gaps["b"] == {"different_source_nets_touch"}


@pytest.mark.parametrize("shape", [
    dict(net=None, figure=dict(lpp=["wire", "drawing"], objType="line", points=[[0, 0], [1, 0]])),
    dict(net="N0", figure=dict(lpp=["wire", "drawing"], objType="arc", points=None)),
    dict(net="N0", figure=dict(lpp=["wire", "drawing"], objType="line", points=[[0, 0], [1, 1]])),
])
def test_incomplete_wire_inventory_cannot_prove_crossing_isolation(shape):
    record = crossing_record()
    record["assets"]["schematic"]["shapes"].append(shape)
    refresh(record)
    assert all(not row["eligible"] for row in compile_reference(record)["nets"].values())


def test_distinct_same_device_same_net_terminals_share_physical_contact():
    record = crossing_record()
    record["topology"]["devices"][0]["pins"].append(dict(name="S", net="n0"))
    geometry = record["assets"]["schematic"]["wire_reference"]["geometry"][0]
    geometry["terminals"].append(dict(name="S", anchors=[dict(id="a0", xy=[0, 0])]))
    result = compile_reference(record)["nets"]["n0"]
    assert result["gaps"] == []
    assert len(result["terminal_contacts"][0]["endpoints"]) == 2


def test_marker_far_from_crossing_does_not_block_isolation():
    record = crossing_record()
    record["assets"]["schematic"]["shapes"].append(dict(net=None, figure=dict(
        lpp=["wire", "drawing"], objType="ellipse", bBox=[[6, 12], [8, 14]])))
    refresh(record)
    assert all(row["eligible"] for row in compile_reference(record)["nets"].values())


def test_target_crossings_are_still_rejected():
    paths = [dict(net="H", owners=[], points=[[0, 0], [10, 0]])]
    members = [dict(points=[[0, 0]]), dict(points=[[10, 0]])]
    reserved = [dict(net="V", points=[[5, -5], [5, 5]])]
    assert "template_paths_different_nets_touch" in path_gaps(
        paths, members, dict(grid=1), {}, [], reserved)


def test_capture_v2_attestation_is_opt_in_and_schema_checked():
    header = dict(asset="schematic", dbu_per_uu=1000)
    rows = v2_rows()
    placement = dict(anchor=dict(source_xy_dbu=[0, 0]),
                     instances=[dict(source_name="M0", device="d0")], shapes=[])
    legacy = capture_reference(header, rows, placement)
    assert legacy["schema"] == "cad.template.wire-reference.v1"
    header["wire_net_semantics"] = NET_SEMANTICS
    validate_geometry_capture(header, rows)
    current = capture_reference(header, rows, placement)
    assert current["schema"] == VERSION
    assert current["wire_shapes_digest"] == digest([])
    assert digest(current) != digest(legacy)
    header["wire_net_semantics"] = "unknown"
    with pytest.raises(TemplateError, match="net semantics"):
        validate_geometry_capture(header, rows)


def test_instance_and_net_renaming_does_not_change_contact_decisions():
    record = crossing_record()
    renamed = copy.deepcopy(record)
    for row in renamed["topology"]["nets"]:
        row["source_name"] += "_renamed"
    for row in renamed["assets"]["schematic"]["shapes"]:
        row["net"] += "_renamed"
    for row in renamed["assets"]["schematic"]["instances"]:
        row["source_name"] = "unrelated-name"
    refresh(renamed)
    assert compile_reference(renamed)["nets"] == compile_reference(record)["nets"]


def test_contact_diagnostics_have_bounded_work_and_output():
    with pytest.raises(TemplateError, match="marker/terminal budget"):
        source_contacts({}, {}, observed=True, markers=[[[0, 0], [1, 1]]] * 2049)
    lines = {"h": [segment([-1, y], [100, y], y) for y in range(65)],
             "v": [segment([x, -1], [x, 100], 100+x) for x in range(65)]}
    with pytest.raises(TemplateError, match="4096 wire crossings"):
        source_contacts(lines, {}, observed=True)


def test_v3_qualification_accepts_v2_reference_and_rechecks_shape_digest():
    from template_adapt_fixture import reusable

    from cadai.template_reuse_build import make_reusable
    from cadai.template_source_qualification import require_qualified

    base = reusable()
    asset = base["assets"]["schematic"]
    asset["wire_reference"].update(schema=VERSION, **capture_contact_evidence(
        {"wire_net_semantics": NET_SEMANTICS}, asset["shapes"]))
    classifications = {d["id"]: d["classification_evidence"] for d in base["topology"]["devices"]}
    current = make_reusable(base, base["reuse_contract"], classifications)
    require_qualified(current)
    base["assets"]["schematic"]["shapes"].append(dict(net=None, figure=dict(objType="label")))
    with pytest.raises(TemplateError, match="geometry|qualification"):
        make_reusable(base, base["reuse_contract"], classifications)
