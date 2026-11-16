"""Symbol capability is independent, but requires a complete saved interface and anchors."""

import pytest
from template_qualification_fixture import collect_rows, symbol_rows

from cadai.template_schema import TemplateError
from cadai.template_standard.replay import replay
from cadai.template_standard.symbol_checks import validate_receipt


@pytest.mark.parametrize("schematic", [False, True])
def test_symbol_interface_qualifies_independently(tmp_path, schematic):
    result, item = collect_rows(tmp_path, symbol_rows(schematic=schematic))
    docs = item["documents"]
    assert result["capabilities"]["symbol-reference-v1"] == "passed"
    assert ("topology" in docs) == schematic
    assert next(iter(docs["symbol"]["labels"].values())) == dict(
        text='(progn "literal")',
        xy=[200, 200],
        bbox=[[100, 100], [300, 300]],
        kind="dynamic_literal",
    )
    assert (docs["symbol"]["source"] == "symbol_interface") != schematic
    replay(item)


@pytest.mark.parametrize(
    "fault",
    [
        "pin",
        "direction",
        "width",
        "extra",
        "missing",
        "pin_shape",
        "rounding",
        "partial",
        "bad_outline",
    ],
)
def test_symbol_gaps_do_not_block_valid_schematic(tmp_path, fault):
    rows = symbol_rows(schematic=True)
    start = next(i for i, r in enumerate(rows) if r.get("asset") == "symbol")
    ports = [r for r in rows[start:] if r["kind"] == "terminal"]
    pins = [r for r in rows[start:] if r["kind"] == "pin"]
    if fault == "pin":
        rows.remove(pins[0])
    elif fault == "direction":
        ports[0]["direction"] = "switch"
    elif fault == "width":
        ports[0]["numBits"] = 2
    elif fault == "extra":
        ports[0]["name"] = pins[0]["terminal"] = "EXTRA"
    elif fault == "missing":
        rows.remove(ports[0])
        rows.remove(pins[0])
    elif fault == "pin_shape":
        pins[0]["figure"]["objType"] = "polygon"
    elif fault == "rounding":
        pins[0]["figure"]["bBox"][0][0] = -0.999
    elif fault == "partial":
        rows[start]["incomplete_features"] = ["partial_graphics"]
    else:
        next(r for r in rows[start:] if r["kind"] == "shape")["figure"]["points"] = [[0, 0]]
    result, item = collect_rows(tmp_path, rows)
    assert result["capabilities"]["symbol-reference-v1"] == "unknown"
    assert result["capabilities"]["topology-v1"] == "passed"
    assert any(g["part"] == "symbol.json" for g in item["documents"]["collection"]["gaps"].values())


@pytest.mark.parametrize("fault", ["anchor", "direction", "width", "coverage"])
def test_reader_rejects_forged_symbol_pass(tmp_path, fault):
    _, item = collect_rows(tmp_path, symbol_rows(schematic=True))
    docs = item["documents"]
    symbol = docs["symbol"]
    if fault == "anchor":
        symbol["anchors"].clear()
    elif fault == "coverage":
        symbol["terminals"]["p0"].pop("port")
    else:
        symbol["terminals"]["p0"][fault] = "output" if fault == "direction" else 8
    with pytest.raises(TemplateError, match="passed symbol"):
        validate_receipt(docs)


@pytest.mark.parametrize("width", [8, 7])
def test_independent_symbol_bus_width_matches_literal_interface(tmp_path, width):
    rows = symbol_rows()
    next(r for r in rows if r["kind"] == "terminal").update(name="DATA<7:0>", numBits=width)
    next(r for r in rows if r["kind"] == "pin")["terminal"] = "DATA<7:0>"
    result, _ = collect_rows(tmp_path, rows)
    assert result["capabilities"]["symbol-reference-v1"] == ("passed" if width == 8 else "unknown")


def test_live_scope_declarations_do_not_masquerade_as_missing_symbol_facts(tmp_path):
    rows = symbol_rows()
    rows[1]["incomplete_features"] = [
        "hierarchy_not_expanded",
        "inherited_connections_not_resolved",
        "bus_members_not_expanded",
        "dynamic_text_not_evaluated",
        "master_geometry_not_captured",
    ]
    result, _ = collect_rows(tmp_path, rows)
    assert result["capabilities"]["symbol-reference-v1"] == "passed"


def test_symbol_only_v1_capture_keeps_direct_interface_support(tmp_path):
    from cadai.template_capture import RECORD_FIELDS

    rows = [r for r in symbol_rows() if r["kind"] != "dependency_guard"]
    rows[0]["schema_version"] = "cad.template.capture.v1"
    # Explicit legacy capture v1 remains readable, with its own footer schema.
    from template_extract_fixture import CaptureClient

    from cadai.template_catalog import call_template

    for row in rows:
        if row["kind"] == "asset_footer":
            row["counts"] = {k: v for k, v in row["counts"].items() if k in RECORD_FIELDS}
    result = call_template(
        "extract_circuit_templates",
        dict(
            library="demo", cell="INV", assets=["symbol"], capture_schema="cad.template.capture.v1"
        ),
        workspace=tmp_path,
        client=CaptureClient(tmp_path / "spool", rows),
    )
    assert result["capabilities"]["symbol-reference-v1"] == "passed"


def _append_symbol_graphics(rows, figures):
    last = max(i for i, r in enumerate(rows) if r["kind"] == "shape")
    rows[last + 1:last + 1] = [dict(kind="shape", figure=figure) for figure in figures]


def test_ellipse_and_arc_graphics_qualify_symbol_reference(tmp_path):
    rows = symbol_rows(schematic=True)
    start = next(i for i, r in enumerate(rows) if r.get("asset") == "symbol")
    next(r for r in rows[start:] if r["kind"] == "pin")["figure"]["objType"] = "ellipse"
    _append_symbol_graphics(
        rows,
        [
            dict(objType="ellipse", bBox=[[10, -2], [14, 2]], lpp=["device", "drawing"]),
            dict(
                objType="arc",
                bBox=[[20, -2], [24, 2]],
                ellipseBBox=[[20, -3], [24, 1]],
                lpp=["device", "drawing"],
                startAngle=-1.570796370506287,
                stopAngle=1.570796370506287,
            ),
        ],
    )
    result, item = collect_rows(tmp_path, rows)
    assert result["capabilities"]["symbol-reference-v1"] == "passed"
    outline = item["documents"]["symbol"]["outline"]
    assert {row["kind"] for row in outline.values()} >= {"ellipse", "arc"}
    arc = next(row for row in outline.values() if row["kind"] == "arc")
    box = arc["ellipse_bbox"]
    assert all(type(v) is int for p in box for v in p)
    assert box[0][0] < box[1][0] and box[0][1] < box[1][1]
    assert arc["start_angle"] == -1.570796370506287


def test_incomplete_arc_graphics_keep_symbol_unknown(tmp_path):
    rows = symbol_rows()
    _append_symbol_graphics(
        rows,
        [
            dict(
                objType="arc",
                bBox=[[20, -2], [24, 2]],
                ellipseBBox=[[20, -3], [24, 1]],
                lpp=["device", "drawing"],
            ),
        ],
    )
    result, item = collect_rows(tmp_path, rows)
    assert result["capabilities"]["symbol-reference-v1"] == "unknown"
    assert any(
        g["part"] == "symbol.json" for g in item["documents"]["collection"]["gaps"].values()
    )


def test_forged_ellipse_geometry_is_rejected(tmp_path):
    rows = symbol_rows()
    _append_symbol_graphics(
        rows, [dict(objType="ellipse", bBox=[[10, -2], [14, 2]], lpp=["device", "drawing"])]
    )
    _, item = collect_rows(tmp_path, rows)
    docs = item["documents"]
    row = next(r for r in docs["symbol"]["outline"].values() if r["kind"] == "ellipse")
    row["ellipse_bbox"] = [[1400, 200], [1000, -200]]
    with pytest.raises(TemplateError, match="inverted bounding box"):
        validate_receipt(docs, sources=item["tables"]["sources"]["items"])


def test_independent_symbol_evidence_binds_capture_and_interface(tmp_path):
    result, item = collect_rows(tmp_path, symbol_rows())
    docs = item["documents"]
    sources = item["tables"]["sources"]["items"]
    assert docs["symbol"]["source"] == "symbol_interface"
    assert docs["symbol"]["depends_on"] == ["capture"]
    assert sources["symbol_interface"]["method"] == "saved-symbol-interface-v1"
    assert sources["symbol_interface"]["evidence_ref"].startswith("symbol-interface:sha256-jcs:")
    assert docs["qualification"]["checks"]["symbol-reference-v1"]["validator"].endswith(
        "-symbol-interface-v1"
    )
    assert result["capabilities"]["symbol-reference-v1"] == "passed"
    validate_receipt(docs, sources=sources)


@pytest.mark.parametrize(
    "fault",
    ["source", "depends_on", "missing_source", "evidence_ref", "terminal", "capture"],
)
def test_forged_independent_symbol_evidence_is_rejected(tmp_path, fault):
    _, item = collect_rows(tmp_path, symbol_rows())
    docs = item["documents"]
    sources = item["tables"]["sources"]["items"]
    if fault == "source":
        docs["symbol"]["source"] = "capture"
    elif fault == "depends_on":
        docs["symbol"]["depends_on"] = []
    elif fault == "missing_source":
        sources.pop("symbol_interface")
    elif fault == "evidence_ref":
        sources["symbol_interface"]["evidence_ref"] = "symbol-interface:sha256-jcs:deadbeef"
    elif fault == "terminal":
        docs["symbol"]["terminals"]["p0"]["width"] = 2
    else:
        sources["capture"]["scope_digest"] = "sha256:" + "0" * 64
    with pytest.raises(TemplateError, match="passed symbol"):
        validate_receipt(docs, sources=sources)


def test_historical_independent_symbol_receipt_remains_readable(tmp_path):
    _, item = collect_rows(tmp_path, symbol_rows())
    docs = item["documents"]
    sources = item["tables"]["sources"]["items"]
    sources["symbol_interface"].pop("evidence_ref")
    docs["qualification"]["checks"]["symbol-reference-v1"]["validator"] = (
        "sico-flat-collection-v3"
    )
    validate_receipt(docs, sources=sources)
