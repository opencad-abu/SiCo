"""Outline planning and public MCP boundary regression against captured gate assets."""

import copy
import math
import os
from pathlib import Path

import pytest

from cadai.template_catalog import TemplateCatalog
from cadai.template_coords import (
    BOX_KEYS,
    LENGTH_KEYS,
    POINT_KEYS,
    POINT_LIST_KEYS,
    is_integer_space,
)
from cadai.template_preview import render_preview
from cadai.template_schema import TemplateUnavailable
from cadai.template_symbol import TOOLS, call_symbol
from cadai.template_symbol_plan import make_plan, write_preview

CATALOG = Path(
    os.environ.get(
        "CAD_AI_TEST_GATE_CATALOG",
        Path(__file__).resolve().parents[2] / "reference/circuit_templates",
    )
)


def gate_record(cell="INVX1", *, labels=False):
    catalog = TemplateCatalog(CATALOG)
    result = catalog.query(
        dict(
            library="gsclib045",
            cell=cell,
            required_asset="symbol",
            offset=0,
            limit=1,
            data_format="legacy",
        )
    )
    rows = result.get("items") or result.get("templates")
    record = catalog.get(rows[0]["template_ref"])
    if not labels:
        # These geometry tests do not exercise source-text fidelity; the
        # unsupported-label refusal is covered separately.
        record["assets"]["symbol"]["shapes"] = [
            shape
            for shape in record["assets"]["symbol"]["shapes"]
            if (shape.get("figure") or {}).get("objType") != "label"
        ]
    return record


def args(**extra):
    return dict(library="drawWork", cell="inverter", port_map={}, style="template_outline", **extra)


@pytest.mark.parametrize("cell", ["INVX1", "NAND2X1", "NOR2X1", "XOR2X1"])
def test_captured_outline_plan(cell, tmp_path):
    record = gate_record(cell)
    plan = make_plan(record, args(outline_scale=4))
    assert plan == make_plan(record, args(outline_scale=4))
    assert plan["style"] == "template_outline"
    assert plan["added_ports"] == ["VDD", "VSS"]
    assert len(plan["rows"]) == len(record["topology"]["ports"])
    assert any(s[0] == "ellipse" for s in plan["body"]) or cell == "XOR2X1"
    if cell != "INVX1":
        assert any(s[0] == "arc" for s in plan["body"])
    assert plan["preview_digest"] != make_plan(record, args())["preview_digest"]
    artifacts = write_preview(plan, tmp_path)
    assert all(Path(a["path"]).is_file() for a in artifacts)
    svg = Path(artifacts[1]["path"]).read_text()
    assert "[@partName]" in svg and "cdsName()" in svg and "[@cellName]" in svg
    assert plan["font"] == "stick"
    # Body includes line segments; selection must not collapse to the inversion bubble.
    pts = [p for s in plan["body"] for p in s[1]] + [r[5:7] for r in plan["rows"]]
    assert plan["selection_box"] == [
        [min(p[i] for p in pts) for i in (0, 1)],
        [max(p[i] for p in pts) for i in (0, 1)],
    ]


def test_unrendered_source_label_is_refused():
    record = gate_record("INVX1", labels=True)
    assert any(
        (shape.get("figure") or {}).get("text") == "[@info]"
        for shape in record["assets"]["symbol"]["shapes"]
    )
    with pytest.raises(
        TemplateUnavailable, match=r"source symbol labels are not rendered: \[@info\]"
    ):
        make_plan(record, args())


def test_drop_label_policy_records_unrendered_labels_and_keeps_outline():
    record = gate_record("INVX1", labels=True)
    plan = make_plan(record, {**args(), "style": "auto", "label_policy": "drop"})
    assert plan["style"] == "template_outline"
    assert plan["fallback_reason"] is None
    assert plan["label_policy"] == "drop"
    assert "[@info]" in plan["dropped_labels"]
    strict = make_plan(record, {**args(), "style": "auto"})
    assert strict["style"] == "rectangular_module"
    assert strict["fallback_reason"]["code"] == "outline_unavailable"


def test_outline_renaming_and_refusals():
    record = gate_record()
    renamed = make_plan(record, {**args(), "port_map": {"A": "IN", "Y": "OUT"}})
    assert [r[0] for r in renamed["rows"]] == ["IN", "VDD", "VSS", "OUT"]
    bad = copy.deepcopy(record)
    bad["assets"]["symbol"]["ports"][0]["direction"] = "output"
    with pytest.raises(TemplateUnavailable, match="direction"):
        make_plan(bad, args())
    bad = copy.deepcopy(record)
    bad["assets"]["symbol"]["shapes"].append(
        {"figure": dict(objType="path", lpp=["device", "drawing"])}
    )
    with pytest.raises(TemplateUnavailable, match="unsupported"):
        make_plan(bad, args())
    bad = copy.deepcopy(record)
    bad["assets"]["symbol"]["instances"] = [{}]
    with pytest.raises(TemplateUnavailable, match="flat"):
        make_plan(bad, args())


def test_auto_uses_captured_outline_and_reports_no_fallback():
    plan = make_plan(gate_record(), {**args(), "style": "auto"})
    assert plan["style"] == "template_outline"
    assert plan["requested_style"] == "auto"
    assert plan["fallback_reason"] is None
    assert plan["body"] and plan["stubs"]


def test_auto_falls_back_explicitly_without_symbol_asset():
    record = copy.deepcopy(gate_record())
    record["assets"].pop("symbol")
    plan = make_plan(record, {**args(), "style": "auto"})
    assert plan["style"] == "rectangular_module"
    assert plan["requested_style"] == "auto"
    assert plan["fallback_reason"]["code"] == "no_symbol_asset"
    with pytest.raises(TemplateUnavailable, match="no captured symbol asset"):
        make_plan(record, args())
    with pytest.raises(TemplateUnavailable, match="no captured symbol asset"):
        make_plan(record, {**args(), "style": "auto", "port_roles": {"A": "power"}})


def test_auto_falls_back_explicitly_on_unsupported_outline():
    record = copy.deepcopy(gate_record())
    record["assets"]["symbol"]["shapes"].append(
        {"figure": dict(objType="path", lpp=["device", "drawing"])}
    )
    plan = make_plan(record, {**args(), "style": "auto"})
    assert plan["style"] == "rectangular_module"
    assert plan["requested_style"] == "auto"
    assert plan["fallback_reason"]["code"] == "outline_unavailable"
    assert "unsupported" in plan["fallback_reason"]["message"]
    with pytest.raises(TemplateUnavailable, match="unsupported"):
        make_plan(record, args())


def test_render_preview_uses_captured_outline(tmp_path):
    result = render_preview(gate_record(), tmp_path)
    report = result["reports"]["symbol_plan"]
    assert report["style"] == "template_outline"
    assert report["fallback_reason"] is None
    assert "captured outline" in Path(result["preview"]).read_text()


def _scaled(value, key=None, factor=1.0):
    if value is None:
        return None
    if key in POINT_KEYS:
        return [v * factor for v in value]
    if key in BOX_KEYS or key in POINT_LIST_KEYS:
        return [[v * factor for v in point] for point in value]
    if key in LENGTH_KEYS:
        return value * factor
    if isinstance(value, dict):
        return {k: _scaled(v, k, factor) for k, v in value.items()}
    if isinstance(value, list):
        return [_scaled(v, None, factor) for v in value]
    return value


def _coordinates(value, key=None):
    if value is None:
        return []
    if key in POINT_KEYS:
        return list(value)
    if key in POINT_LIST_KEYS or key in BOX_KEYS:
        return [n for point in value for n in point]
    if key in LENGTH_KEYS:
        return [value]
    if isinstance(value, dict):
        return [n for k, v in value.items() for n in _coordinates(v, k)]
    if isinstance(value, list):
        return [n for v in value for n in _coordinates(v, None)]
    return []


def test_v2_symbol_geometry_is_integer_dbu_and_plans_identically():
    """Symbol drawing geometry is integer DBU and normalizes back to user units."""
    record = gate_record("INVX1")
    asset = record["assets"]["symbol"]
    assert asset["coordinate_space"] == "source_dbu_integer_relative"
    dbu = asset["dbu_per_uu"]
    assert type(dbu) in (int, float) and dbu > 0
    assert asset["anchor"]["kind"] in {"view_origin", "instance_min"}
    values = _coordinates(asset["pins"]) + _coordinates(asset["shapes"])
    assert values and all(type(n) is int for n in values)
    legacy = copy.deepcopy(record)
    converted = _scaled(copy.deepcopy(asset), None, 1.0 / dbu)
    converted.pop("coordinate_space", None)
    converted["source"] = asset["source"]  # captured provenance header stays verbatim
    legacy["assets"]["symbol"] = converted
    assert make_plan(legacy, args(outline_scale=4)) == make_plan(record, args(outline_scale=4))


def test_draw_symbol_registered_and_uses_retained_plan(tmp_path):
    tool = next(t for t in TOOLS if t["name"] == "draw_symbol")
    assert tool["annotations"]["readOnlyHint"] is False

    class Client:
        def call(self, name, values):
            assert name == "eval_skill_native"
            assert (
                values["code"] == 'aiDrawSymbol("template-symbol-preview:p" "' + "a" * 64 + '" "d")'
            )
            return True, {"ok": True, "value": '{"ok":true}'}

    assert call_symbol(
        "draw_symbol",
        dict(preview_ref="template-symbol-preview:p", preview_digest="a" * 64, request_id="d"),
        workspace=tmp_path,
        client=Client(),
    )["ok"]


def test_polygon_body_stubs_and_missing_direction_policy():
    record = gate_record()
    asset = record["assets"]["symbol"]

    def source_point(point):
        """Express injected user-unit geometry in the asset's own coordinate space."""
        if not is_integer_space(asset):
            return list(point)
        dbu = asset["dbu_per_uu"]
        return [int(round(v * dbu)) for v in point]

    asset["shapes"] = [s for s in asset["shapes"] if s["figure"]["lpp"] != ["device", "drawing"]]
    asset["shapes"] += [
        {
            "figure": dict(
                objType="polygon",
                lpp=["device", "drawing"],
                points=[
                    source_point([0.21875, -0.5]),
                    source_point([0.4875, 0.1875]),
                    source_point([0.21875, 0.5]),
                ],
            )
        },
        # An outward stub extends beyond the body/terminal union and is excluded.
        {
            "figure": dict(
                objType="line",
                lpp=["device", "drawing"],
                points=[source_point([0, 0.1875]), source_point([-1, 0.1875])],
            )
        },
    ]
    next(p for p in record["topology"]["ports"] if p["name"] == "VDD")["direction"] = "inputOutput"
    plan = make_plan(record, args())
    assert plan["selection_box"][0][0] < plan["body_box"][0][0]
    assert next(r for r in plan["rows"] if r[0] == "VDD")[6] == 0.5
    assert next(r for r in plan["rows"] if r[0] == "VSS")[6] == -0.5
    assert len(plan["body"]) == 1 and plan["body"][0][0] == "polygon"


def test_default_remains_rectangular_and_draw_profile_boundary(tmp_path):
    from cadai.mcp import McpServer

    record = gate_record()
    rectangular = make_plan(record, {**args(), "style": "rectangular_module"})
    assert rectangular["style"] == "rectangular_module" and "body" not in rectangular
    full = McpServer(None, workspace=tmp_path).handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )
    from inspect import signature

    if "profile" not in signature(McpServer).parameters:
        return
    restricted = McpServer(None, workspace=tmp_path, profile="verification").handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )
    assert "draw_symbol" in {t["name"] for t in full["result"]["tools"]}
    assert "draw_symbol" not in {t["name"] for t in restricted["result"]["tools"]}


def test_power_top_ground_bottom_with_explicit_and_semantic_roles():
    record = gate_record()
    plan = make_plan(record, args(port_roles={"VDD": "power", "VSS": "ground"}))
    pins = {p["name"]: p for p in plan["outline_pins"]}
    assert pins["VDD"]["side"] == "top" and pins["VSS"]["side"] == "bottom"
    assert pins["VDD"]["origin"][1] > plan["body_box"][1][1]
    assert pins["VSS"]["origin"][1] < plan["body_box"][0][1]
    # Metadata, rather than terminal spelling, also places power/ground correctly.
    for net in record["topology"]["nets"]:
        if net["source_name"] == "VDD!":
            net["sig_type"] = "supply"
        if net["source_name"] == "VSS!":
            net["sig_type"] = "ground"
    plan = make_plan(record, {**args(), "port_map": {"VDD": "RAIL_P", "VSS": "RETURN"}})
    assert {p["name"]: p["side"] for p in plan["outline_pins"]} == {
        "A": "left",
        "Y": "right",
        "RAIL_P": "top",
        "RETURN": "bottom",
    }
    assert all(p["global_net"] for p in plan["ports"] if p["name"] in {"RAIL_P", "RETURN"})


def test_role_maps_require_known_source_ports_and_outline_style():
    from cadai.template_schema import TemplateError

    with pytest.raises(TemplateError, match="source schematic"):
        make_plan(gate_record(), args(port_roles={"UNKNOWN": "power"}))
    with pytest.raises(TemplateError, match="style"):
        make_plan(
            gate_record(), {**args(port_roles={"VDD": "power"}), "style": "rectangular_module"}
        )


def test_outline_suggests_global_rail_roles_without_applying():
    plan = make_plan(gate_record(), args())
    suggestions = {row["source_name"]: row for row in plan["port_role_suggestions"]}
    assert suggestions["VDD"]["suggested_role"] == "power"
    assert suggestions["VSS"]["suggested_role"] == "ground"
    assert suggestions["VDD"]["applied"] is False
    assert suggestions["VSS"]["requires_confirmation"] is True
    assert "A" not in suggestions and "Y" not in suggestions
    assert plan["port_roles"] == {}
    pins = {pin["name"]: pin for pin in plan["outline_pins"]}
    assert pins["VDD"]["side"] == "left" and pins["VSS"]["side"] == "left"
    confirmed = make_plan(gate_record(), args(port_roles={"VDD": "power", "VSS": "ground"}))
    applied = {row["source_name"]: row for row in confirmed["port_role_suggestions"]}
    assert applied["VDD"]["applied"] is True
    assert applied["VDD"]["basis"] == "explicit_port_roles"


def test_existing_supply_pins_are_relocated_and_old_stubs_removed():
    record = gate_record()
    plan = make_plan(record, args(port_roles={"A": "power", "Y": "ground"}))
    pins = {p["name"]: p for p in plan["outline_pins"]}
    assert pins["A"]["side"] == "top" and not pins["A"]["added"]
    assert pins["Y"]["side"] == "bottom" and not pins["Y"]["added"]
    old_anchors = [[0, 0.1875], [0.75, 0.1875]]
    assert not any(p in old_anchors for s in plan["stubs"] for p in s[1])


@pytest.mark.parametrize("scale", [1, 4, 32])
@pytest.mark.parametrize("roles", [{}, {"VDD": "power", "VSS": "ground"}])
def test_all_outline_terminal_stubs_touch_body_and_are_five_grid_steps_max(scale, roles):
    for cell in ("INVX1", "NAND2X1", "NOR2X1", "XOR2X1"):
        plan = make_plan(gate_record(cell), args(outline_scale=scale, port_roles=roles))
        for _, points, _ in plan["stubs"]:
            assert (
                math.hypot(points[0][0] - points[1][0], points[0][1] - points[1][1])
                <= 5 * plan["grid"] + 1e-7
            )
            contact = points[1]
            assert body_distance(plan["body"], contact) < 1e-7
        assert len(plan["stubs"]) == len(plan["rows"])
        assert all(
            abs(v / plan["grid"] - round(v / plan["grid"])) < 1e-7
            for row in plan["rows"]
            for v in row[5:7]
        )


def test_analytic_contacts_and_no_bounding_box_fallback():
    from cadai.template_symbol_contacts import attach_stubs, intersections

    triangle = [["polygon", [[0, 0], [2, 1], [0, 2]], None]]
    assert sorted(intersections(triangle, 1, 1)) == [[1, 0.5], [1, 1.5]]
    rect = [["rectangle", [[0, 0], [2, 2]], None]]
    assert sorted(intersections(rect, 0, 1)) == [[0, 1], [2, 1]]
    circle = [["ellipse", [[-1, -1], [1, 1]], None]]
    assert sorted(intersections(circle, 0, 0.6)) == [[-0.8, 0.6], [0.8, 0.6]]
    assert intersections(circle, 0, 1.1) == []
    arc = [["arc", [[0, -1], [1, 1]], [[[-1, -1], [1, 1]], -math.pi / 2, math.pi / 2]]]
    assert intersections(arc, 0, 0) == [[1, 0]]
    # This shape has no visible segment on the requested left side. A bbox
    # contact would silently draw a floating pin; refuse before any OA write.
    with pytest.raises(TemplateUnavailable, match="body contact"):
        attach_stubs(
            [["line", [[2, 0], [2, 2]], None]], [[0, 0], [2, 2]], {"IN": [-1, 1]}, {"IN": "left"}
        )


def test_outline_too_small_for_distinct_grid_pins_refuses():
    with pytest.raises(TemplateUnavailable, match="distinct grid pin"):
        make_plan(
            gate_record("NAND2X1"),
            args(outline_scale=0.25, port_roles={"VDD": "power", "VSS": "ground"}),
        )


def body_distance(body, xy):
    """Independent distance oracle for planned and saved OA outlines."""
    distances = []
    for kind, points, extra in body:
        if kind == "rectangle":
            lo, hi = points
            points = [lo, [hi[0], lo[1]], hi, [lo[0], hi[1]]]
            kind = "polygon"
        if kind in {"line", "polygon"}:
            for a, b in zip(points, points[1:] + (points[:1] if kind == "polygon" else [])):
                dx, dy = b[0] - a[0], b[1] - a[1]
                if dx == dy == 0:
                    continue
                t = min(
                    1, max(0, ((xy[0] - a[0]) * dx + (xy[1] - a[1]) * dy) / (dx * dx + dy * dy))
                )
                distances.append(math.hypot(xy[0] - a[0] - t * dx, xy[1] - a[1] - t * dy))
        if kind in {"ellipse", "arc"}:
            lo, hi = points if kind == "ellipse" else extra[0]
            cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
            rx, ry = (hi[0] - lo[0]) / 2, (hi[1] - lo[1]) / 2
            angle = math.atan2((xy[1] - cy) / ry, (xy[0] - cx) / rx)
            if (
                kind == "arc"
                and (angle - extra[1]) % (2 * math.pi)
                > (extra[2] - extra[1]) % (2 * math.pi) + 1e-7
            ):
                continue
            distances.append(
                math.hypot(xy[0] - cx - rx * math.cos(angle), xy[1] - cy - ry * math.sin(angle))
            )
    return min(distances, default=math.inf)
