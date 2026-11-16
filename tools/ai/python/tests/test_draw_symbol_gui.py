"""Native outline redraw including VIC, saved geometry, labels and instance rendering."""

import copy
import hashlib
import json
import os

import pytest
from circuit_create_support import AI_ROOT
from template_smic28_support import migration_session
from template_symbol_fixture import scratch_schematic
from test_circuit_rc_gui import mcp_call
from test_draw_symbol import CATALOG, body_distance, gate_record

from cadai.template_catalog import TemplateCatalog


@pytest.mark.skipif(
    os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1", reason="licensed Symbol acceptance"
)
def test_draw_symbol_native(tmp_path, monkeypatch):
    with migration_session(tmp_path) as (b, s, proc):
        held = {}
        original_get = TemplateCatalog.get
        monkeypatch.setattr(
            TemplateCatalog,
            "get",
            lambda self, ref: held.get("record") or original_get(self, ref),
        )
        source_path = CATALOG / "catalogs/cadence-custom-gates45-r6.sqlite3"
        source_digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
        for mod in [
            "AI_templates.il",
            "AI_circuit_symbol.il",
            "AI_template_symbol_geometry.il",
            "AI_template_symbol_draw.il",
            "AI_template_symbol.il",
        ]:
            b.skill("load(" + json.dumps(str(AI_ROOT / "skill" / mod)) + ")")
        # Native entry must reject detached/long/mismatched literal plans before
        # creating OA data; a contact inside the rectangle bbox is insufficient.
        probe_body = 'list(list("rectangle" list(0:0 1:1) nil))'
        probe_port = 'list(list("A" "input" 1 nil "signal" -0.3125 0.5 nil))'
        assert (
            b.skill(
                'aiTdValidateStubs(list(list("line" list(-0.3125:0.5 0:0.5) nil)) '
                + probe_body
                + " "
                + probe_port
                + " 0.000001)"
            )
            == "t"
        )
        for invalid in [
            "list(-0.5:0.5 0:0.5)",
            "list(0.25:0.5 0.5:0.5)",
            "list(-0.3125:0.5 0:0.75)",
            "list(-0.125:0.5 0:0.5)",
        ]:
            assert (
                b.skill(
                    'null(errset(aiTdValidateStubs(list(list("line" '
                    + invalid
                    + " nil)) "
                    + probe_body
                    + " "
                    + probe_port
                    + " 0.000001) nil))"
                )
                == "t"
            )
        b.skill(
            'unless(ddGetObj("SicoTest") ddCreateLib("SicoTest" '
            + json.dumps(str(tmp_path / "SicoTest"))
            + "))"
        )
        evidence = []
        for mode in ["foreground", "background"]:
            task = mcp_call(
                s,
                "begin_circuit_task",
                dict(task_id=mode, presentation=mode, choice_confirmed=True),
            )
            for cell in ["INVX1", "NAND2X1", "XOR2X1", "NOR2X1"]:
                record = gate_record(cell)
                held["record"] = record
                name = cell + "_" + mode
                local = copy.deepcopy(record)
                scopes = {}
                for net in local["topology"]["nets"]:
                    if net["is_global"]:
                        scopes[net["id"]] = "local"
                        net.update(is_global=False, source_name=net["source_name"].rstrip("!"))
                scratch_schematic(b, "SicoTest", name, local)
                preview = mcp_call(
                    s,
                    "preview_template_symbol",
                    dict(
                        template_ref=record["template_ref"],
                        task_ref=task["task_ref"],
                        request_id=name,
                        library="SicoTest",
                        cell=name,
                        style="template_outline",
                        port_roles={"VDD": "power", "VSS": "ground"},
                        outline_scale=4 if mode == "foreground" else 1,
                        net_scope_map=scopes,
                    ),
                )
                assert preview.get("ok"), preview
                assert b.skill(f'ddGetObj("SicoTest" "{name}" "symbol")') == "nil"
                create_args = dict(
                    preview_ref=preview["preview_ref"],
                    preview_digest=preview["preview_digest"],
                    request_id=name,
                )
                result = mcp_call(s, "draw_symbol", create_args)
                (tmp_path / (name + ".json")).write_text(
                    json.dumps(dict(preview=preview, created=result), indent=2)
                )
                assert (
                    result["ok"] and result["bootstrap_cleaned"] and result["readback_verified"]
                ), result
                assert result["interface_check"] == [0, 0]
                if mode == "background":
                    assert (
                        b.skill(
                            "length(setof(v dbGetOpenCellViews() "
                            f'aiCircuitCvMatches(v list("SicoTest" "{name}" "symbol"))))'
                        )
                        == "0"
                    )
                assert mcp_call(s, "draw_symbol", create_args) == result
                assert mcp_call(
                    s, "inspect_template_symbol", dict(symbol_ref=result["symbol_ref"])
                )["ok"]
                ent = "aiTemplateSymbols[" + json.dumps(result["symbol_ref"]) + "]"
                snapshot = json.loads(b.skill(f"aiCircuitJson({ent}['snapshot])"))
                figures = [json.loads(f) for f in snapshot[1]]
                dbu = float(
                    b.skill(
                        "let((sdCv sdDbu) sdCv=dbOpenCellViewByType("
                        f'"SicoTest" "{name}" "symbol" "" "r") '
                        "sdDbu=sdCv~>DBUPerUU dbClose(sdCv) sdDbu)"
                    )
                )
                native_body = []
                native_stubs = []
                anchors = [r[5:7] for r in preview["plan"]["rows"]]
                for f in figures:
                    if f.get("lpp") != ["device", "drawing"]:
                        continue
                    if f["objType"] == "line" and f["points"][0] in anchors:
                        native_stubs.append(f["points"])
                    else:
                        native_body.append(
                            [
                                {"rect": "rectangle"}.get(f["objType"], f["objType"]),
                                f["points"] if f["objType"] in {"line", "polygon"} else f["bBox"],
                                [f["ellipseBBox"], f["startAngle"], f["stopAngle"]]
                                if f["objType"] == "arc"
                                else None,
                            ]
                        )
                assert len(native_stubs) == len(anchors)
                for a, contact in native_stubs:
                    assert (
                        0
                        < sum((a[i] - contact[i]) ** 2 for i in (0, 1)) ** 0.5
                        <= 5 * preview["plan"]["grid"] + 1e-8
                    )
                    assert body_distance(native_body, contact) <= 1 / dbu
                labels = [f for f in figures if f["objType"] == "label"]
                assert len(
                    [f for f in labels if (f.get("text") or "").startswith("cdsTerm(")]
                ) == len(record["topology"]["ports"])
                assert len([f for f in labels if f.get("text") == "cdsName()"]) == 1
                assert all(f["font"] == "stick" and f["height"] == 0.0625 for f in labels)
                assert any(f.get("text") == "[@partName]" for f in labels)
                assert any(
                    f.get("text") == "[@cellName]"
                    and f.get("labelType") == "NLPLabel"
                    and f.get("justify") == "lowerRight"
                    for f in labels
                )
                assert any(
                    f.get("text") == "cdsName()" and f.get("labelType") == "ILLabel" for f in labels
                )
                native_arcs = sorted(
                    (f["startAngle"], f["stopAngle"]) for f in figures if f["objType"] == "arc"
                )
                planned_arcs = sorted(
                    (r[2][1], r[2][2]) for r in preview["plan"]["body"] if r[0] == "arc"
                )
                assert len(native_arcs) == len(planned_arcs)
                for actual, expected in zip(native_arcs, planned_arcs):
                    assert actual == pytest.approx(expected, abs=1e-6)
                boxes = [f["bBox"] for f in figures if f.get("lpp") == ["instance", "drawing"]]
                assert boxes == [preview["plan"]["selection_box"]]
                roles = {p["name"]: p for p in preview["plan"]["outline_pins"]}
                assert roles["VDD"]["side"] == "top" and roles["VSS"]["side"] == "bottom"
                assert roles["VDD"]["origin"][1] > preview["plan"]["body_box"][1][1]
                assert roles["VSS"]["origin"][1] < preview["plan"]["body_box"][0][1]
                assert snapshot[-1] == ["partName", name]
                if mode == "foreground":
                    png = json.dumps(str(tmp_path / (name + ".png")))
                    b.skill(
                        f"hiExportImage(?window {ent}['window] ?fileName {png} "
                        "?width 1400 ?height 950)"
                    )
                evidence.append(dict(result=result, snapshot=snapshot))
        b.skill(
            'let((c m) c=dbOpenCellViewByType("SicoTest" "symbol_usage" '
            '"schematic" "schematic" "a") '
            'foreach(pair list(list("INVX1_foreground" 0:0) '
            'list("NAND2X1_foreground" 8:0) list("NOR2X1_foreground" 16:0)) '
            'm=dbOpenCellViewByType("SicoTest" car(pair) "symbol" "" "r") '
            'schCreateInst(c m strcat("I_" car(pair) "_0") cadr(pair) "R0") dbClose(m)) '
            "dbSave(c) dbClose(c) t)"
        )
        b.skill(
            'drawUsage=deOpenCellView("SicoTest" "symbol_usage" "schematic" "schematic" nil "r")'
        )
        b.skill("hiZoomIn(drawUsage list(-2:-3 23:4))")
        b.skill(
            "hiExportImage(?window drawUsage ?fileName "
            + json.dumps(str(tmp_path / "usage.png"))
            + " ?width 1800 ?height 750)"
        )
        b.skill("hiZoomIn(drawUsage list(-2:-2 5:3))")
        b.skill(
            "hiExportImage(?window drawUsage ?fileName "
            + json.dumps(str(tmp_path / "usage-inv.png"))
            + " ?width 1400 ?height 950)"
        )
        # Existing symbols and wrong-style previews must never be cleared.
        refused = mcp_call(s, "draw_symbol", {**create_args, "request_id": "existing"})
        assert not refused["ok"] and not refused["view_created"]
        plain = gate_record()
        local = copy.deepcopy(plain)
        for net in local["topology"]["nets"]:
            if net["is_global"]:
                net.update(is_global=False, source_name=net["source_name"].rstrip("!"))
        scratch_schematic(b, "SicoTest", "rect_preview", local)
        rectangle = mcp_call(
            s,
            "preview_template_symbol",
            dict(
                template_ref=plain["template_ref"],
                task_ref=task["task_ref"],
                request_id="rectangle",
                library="SicoTest",
                cell="rect_preview",
                net_scope_map={
                    n["id"]: "local" for n in plain["topology"]["nets"] if n["is_global"]
                },
            ),
        )
        assert rectangle["ok"], rectangle
        refused = mcp_call(
            s,
            "draw_symbol",
            dict(
                preview_ref=rectangle["preview_ref"],
                preview_digest=rectangle["preview_digest"],
                request_id="wrong_style",
            ),
        )
        assert refused["code"] == "outline_preview_required"
        assert b.skill('ddGetObj("SicoTest" "rect_preview" "symbol")') == "nil"
        b.skill(
            'let((v) v=dbOpenCellViewByType("SicoTest" "NOR2X1_background" "symbol" "" "a") '
            'schCreateSymbolShape(v "line" "outline" list(0:0 1:1)) dbSave(v) dbClose(v) t)'
        )
        assert (
            mcp_call(s, "inspect_template_symbol", dict(symbol_ref=result["symbol_ref"]))["code"]
            == "symbol_changed"
        )
        assert hashlib.sha256(source_path.read_bytes()).hexdigest() == source_digest
        # Verify loader registration and repeated loading in this owned session.
        b.skill("procedure(witMenuToolsRoot() " + json.dumps(str(AI_ROOT.parent)) + ")")
        for _ in range(2):
            b.skill("load(" + json.dumps(str(AI_ROOT / "skill++/AI.ils")) + ")")
        assert b.skill("aiTemplateSymbolRevision()") == "20260923.ai.template.symbol.v7"
        assert b.skill("aiLoaderRevision()") == "20260924.ai.loader.v34"
        assert b.skill("aiTemplatesRevision()") == "20261009.ai.templates.v18"
        assert b.skill("aiResolveRoot()") == str(AI_ROOT)
        assert b.skill("isCallable('aiDrawSymbol)") == "t"
        (tmp_path / "evidence.json").write_text(json.dumps(evidence, indent=2))
