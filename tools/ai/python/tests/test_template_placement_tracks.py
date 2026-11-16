"""Source tracks survive target resizing, mirroring, remapping and missing columns."""

import copy

import pytest
from test_template_placement import DEVICE_MAP, fixture, relation_view

from cadai.circuit_geometry_schema import transform, transform_box
from cadai.circuit_spec_schema import CircuitSpecError
from cadai.template_device_axes import device_axes
from cadai.template_placement import plan_layout
from cadai.template_placement_tracks import place_tracks
from cadai.template_project import _anonymize_derived


def source_asset():
    terminals = [{"name": n, "anchors": [{"xy": p}]} for n, p in (
        ("D", [40, 30]), ("S", [40, -30]), ("B", [40, -30]), ("G", [0, 0]))]
    return dict(coordinate_space="source_dbu_integer_relative", dbu_per_uu=160,
                instances=[dict(device="d0", relative_xy=[80, 0], orient="MY")],
                wire_reference=dict(geometry=[dict(device="d0", terminals=terminals)]))


def track_fixture():
    asset = source_asset()
    asset["instances"].append(dict(device="d1", relative_xy=[40, 300], orient="R0"))
    asset["wire_reference"]["geometry"].append(dict(device="d1", terminals=[
        dict(name=n, anchors=[dict(xy=p)]) for n, p in (("P", [0, 0]), ("N", [0, -60]))]))
    reference = dict(dbu_per_uu=160, device_axes=device_axes(asset), instances=[
        dict(device=i["device"], xy=i["relative_xy"]) for i in asset["instances"]])
    geometry = {
        "mos": dict(occupied_bbox=[[-.25, -.25], [.5, .25]], terminals=[
            dict(name=n, anchors=[dict(xy=p)]) for n, p in (
                ("D", [.375, .25]), ("S", [.375, -.25]), ("B", [.375, -.25]))]),
        "res": dict(occupied_bbox=[[-.25, -.5], [.25, .125]], terminals=[
            dict(name=n, anchors=[dict(xy=p)]) for n, p in (("P", [0, 0]), ("N", [0, -.5]))]),
    }
    return reference, geometry


def solve(reference, geometry, mapping=None):
    gaps = []
    layout, tracks = place_tracks(geometry, {"mos": "MY", "res": "R0"}, reference,
                                  {"d0": "mos", "d1": "res"}, .0625, gaps, 1, 1,
                                  terminal_map=mapping)
    return layout, tracks, gaps


def test_distinct_terminal_points_define_axes_after_source_mirroring():
    assert device_axes(source_asset()) == [dict(device="d0", axes={
        "x": dict(coordinate=40, terminals=["B", "D", "S"])})]


def test_coincident_and_ambiguous_terminal_points_do_not_invent_an_axis():
    asset = source_asset()
    terms = asset["wire_reference"]["geometry"][0]["terminals"]
    terms[0]["anchors"][0]["xy"] = [40, -30]
    assert device_axes(asset) == []  # Three names at one point do not define a line.
    terms[:] = [dict(name=str(i), anchors=[dict(xy=p)]) for i, p in enumerate(
        ([0, 0], [0, 40], [40, 0], [40, 40]))]
    assert device_axes(asset) == []  # Two equally supported parallel lines.
    asset.pop("coordinate_space")
    assert device_axes(asset) == []


def test_terminal_alignment_uses_target_offsets_and_explicit_renaming():
    reference, geometry = track_fixture()
    geometry["mos"]["terminals"][0]["name"] = "drain"
    layout, tracks, gaps = solve(reference, geometry, {
        "d0": dict(D="drain", S="S", B="B"), "d1": dict(P="P", N="N")})
    assert not gaps
    mos_d = transform([.375, .25], "MY", layout["mos"]["xy"])
    res_n = transform([0, -.5], "R0", layout["res"]["xy"])
    assert mos_d[0] == res_n[0]
    assert layout["mos"]["xy"][0] - layout["res"]["xy"][0] == .375
    assert res_n[1] > mos_d[1]
    assert next(t for t in tracks if t["axis"] == "x")["members"] == ["mos", "res"]


def test_unknown_or_noncollinear_target_axis_is_reported():
    reference, geometry = track_fixture()
    geometry["mos"]["terminals"][0]["anchors"][0]["xy"][0] += .0625
    _, _, gaps = solve(reference, geometry)
    assert gaps == [dict(code="terminal_axis_unavailable", instance="mos", axis="x",
                         terminals=["B", "D", "S"])]


def test_incompatible_grid_phase_is_rejected_instead_of_rounding_terminals():
    reference, geometry = track_fixture()
    for term in geometry["mos"]["terminals"]:
        term["anchors"][0]["xy"][0] += .01
    with pytest.raises(CircuitSpecError, match="cannot align on target grid"):
        solve(reference, geometry)


def test_missing_left_column_does_not_shift_the_remaining_row_left():
    spec, bindings, geometry = fixture()
    view = relation_view()
    view["instances"][2]["xy"] = [240, 240]
    layout, report = plan_layout(spec, bindings, geometry, view, DEVICE_MAP)
    positions = layout["instances"]
    assert positions["R1"]["xy"][0] == positions["R2"]["xy"][0]
    assert positions["R0"]["xy"][0] < positions["R2"]["xy"][0]
    assert any(t["axis"] == "x" and t["members"] == ["R1", "R2"] for t in report["tracks"])


def test_device_order_and_names_do_not_change_positions():
    spec, bindings, geometry = fixture()
    view = relation_view()
    expected = plan_layout(spec, bindings, geometry, view, DEVICE_MAP)[0]
    names = dict(R0="Z", R1="A", R2="B")
    for instance in spec["instances"]:
        instance["id"] = instance["name"] = names[instance["id"]]
    spec["instances"].reverse()
    for row in geometry["instances"]:
        row["instance"] = names[row["instance"]]
    geometry["instances"].reverse()
    view["instances"].reverse()
    actual = plan_layout(spec, bindings, geometry, view,
                         {s: names[t] for s, t in DEVICE_MAP.items()})[0]
    assert actual["instances"] == {names[k]: v for k, v in expected["instances"].items()}


def test_unmapped_residual_is_below_all_mapped_annotation_boxes():
    spec, bindings, geometry = fixture()
    for row in geometry["instances"]:
        row["annotation_bbox"] = [[-.5, -.5], [.75, .75]]
    layout, _ = plan_layout(spec, bindings, geometry, relation_view(), {"d0": "R0", "d2": "R2"})
    boxes = {g["instance"]: transform_box(g["annotation_bbox"],
             layout["instances"][g["instance"]]["orientation"],
             layout["instances"][g["instance"]]["xy"]) for g in geometry["instances"]}
    assert boxes["R1"][1][1] < min(boxes[k][0][1] for k in ("R0", "R2"))


@pytest.mark.parametrize("mapping", [{"d9": "R0"}, {"d0": "missing"}, {"d0": "R0", "d1": "R0"}])
def test_invalid_device_mapping_is_rejected(mapping):
    spec, bindings, geometry = fixture()
    with pytest.raises(CircuitSpecError, match="device_map"):
        plan_layout(spec, bindings, geometry, relation_view(), mapping)


def test_abstract_projection_does_not_leak_new_axis_coordinates():
    reference, _ = track_fixture()
    before = copy.deepcopy(reference)
    assert "device_axes" not in _anonymize_derived(reference, {}, "L1")
    assert reference == before


def test_public_placement_consumes_verified_terminal_map_and_rejects_override(tmp_path):
    from template_adapt_fixture import geometry_request, install, reusable, target_case

    from cadai.template_circuit import prepare_template
    from cadai.template_placement import preview_placement

    record = reusable()
    install(tmp_path, record)
    prepared = prepare_template(target_case(record, extra=True), workspace=tmp_path)
    geometry = geometry_request(prepared)
    args = {k: geometry[k] for k in ("spec", "bindings", "geometry", "template_use")}
    args["template_ref"] = record["template_ref"]
    result = preview_placement(args, workspace=tmp_path)
    assert result["ok"]
    assert set(result["layout"]["instances"]) == {i["id"] for i in args["spec"]["instances"]}
    args["terminal_map"] = copy.deepcopy(args["template_use"]["terminal_map"])
    args["terminal_map"]["d0"]["D"] = "G"
    with pytest.raises(CircuitSpecError, match="terminal_map differs from verified template_use"):
        preview_placement(args, workspace=tmp_path)


@pytest.mark.parametrize("mapping", [{"d9": {"P": "PLUS"}}, {"d0": {"P": "UNKNOWN"}},
                                     {"d0": {"P": "PLUS", "N": "PLUS"}}])
def test_invalid_terminal_mapping_is_rejected(mapping):
    spec, bindings, geometry = fixture()
    with pytest.raises(CircuitSpecError, match="terminal_map"):
        plan_layout(spec, bindings, geometry, relation_view(), DEVICE_MAP, terminal_map=mapping)


def test_two_devices_on_the_same_source_axes_are_not_silently_separated():
    reference, geometry = track_fixture()
    reference["instances"][1]["xy"][1] = 0
    with pytest.raises(CircuitSpecError, match="share both placement axes"):
        solve(reference, geometry)


def test_incomplete_source_position_remains_an_explicit_residual_gap():
    spec, bindings, geometry = fixture()
    view = relation_view()
    view["instances"].pop()
    view["gaps"] = [dict(code="placement_incomplete", device="d2")]
    layout, report = plan_layout(spec, bindings, geometry, view, DEVICE_MAP)
    assert layout["instances"]["R2"]["xy"][1] < layout["instances"]["R0"]["xy"][1]
    assert view["gaps"][0] in report["gaps"]
