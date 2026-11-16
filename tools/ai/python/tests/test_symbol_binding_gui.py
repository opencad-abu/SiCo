"""Retained Symbol export freshness guards without PDK discovery or simulation."""

import copy
import json
import os

import pytest
from circuit_create_support import AI_ROOT, session
from template_symbol_fixture import scratch_schematic
from test_circuit_rc_gui import mcp_call
from test_draw_symbol import gate_record


@pytest.mark.skipif(os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1", reason="licensed Symbol binding")
def test_native_symbol_binding_freshness(tmp_path):
    with session(tmp_path) as (b, s, _):
        for module in [
            "AI_templates.il",
            "AI_circuit_symbol.il",
            "AI_template_symbol_geometry.il",
            "AI_template_symbol_draw.il",
            "AI_template_symbol.il",
            "AI_template_symbol_binding.il",
        ]:
            b.skill("load(" + json.dumps(str(AI_ROOT / "skill" / module)) + ")")
        source = gate_record()
        local = copy.deepcopy(source)
        scopes = {}
        for net in local["topology"]["nets"]:
            if net["is_global"]:
                scopes[net["id"]] = "local"
                net.update(is_global=False, source_name=net["source_name"].rstrip("!"))
        scratch_schematic(b, "genericWork", "guard", local)
        task = mcp_call(
            s,
            "begin_circuit_task",
            dict(task_id="binding", presentation="background", choice_confirmed=True),
        )
        pre = mcp_call(
            s,
            "preview_template_symbol",
            dict(
                library="genericWork",
                cell="guard",
                template_ref=source["template_ref"],
                task_ref=task["task_ref"],
                request_id="guard",
                style="template_outline",
                net_scope_map=scopes,
                port_roles={"VDD": "power", "VSS": "ground"},
            ),
        )
        assert pre["ok"], pre
        created = mcp_call(
            s,
            "draw_symbol",
            dict(
                preview_ref=pre["preview_ref"],
                preview_digest=pre["preview_digest"],
                request_id="guard",
            ),
        )
        assert created["ok"], created
        args = dict(symbol_ref=created["symbol_ref"], master_id="dutMaster", instance_id="dut")
        open_before = b.skill("length(dbGetOpenCellViews())")
        before = mcp_call(s, "get_symbol_binding", args)
        assert before["ok"], before
        assert mcp_call(s, "get_symbol_binding", args) == before
        assert {p["name"]: p["anchors"][0]["escape"] for p in before["geometry"]["terminals"]} == {
            "A": "left",
            "Y": "right",
            "VDD": "up",
            "VSS": "down",
        }
        assert b.skill("length(dbGetOpenCellViews())") == open_before
        assert (
            b.skill(
                "length(setof(v dbGetOpenCellViews() "
                'aiCircuitCvMatches(v list("genericWork" "guard" "symbol"))))'
            )
            == "0"
        )
        assert (
            mcp_call(s, "get_symbol_binding", {**args, "symbol_ref": "template-symbol:missing"})[
                "code"
            ]
            == "symbol_not_ready"
        )
        # Unsaved and saved modifications both invalidate the retained reference.
        b.skill('holdSymbol=dbOpenCellViewByType("genericWork" "guard" "symbol" "" "a")')
        b.skill('dbReplaceProp(holdSymbol "partName" "string" "changed")')
        assert not mcp_call(s, "get_symbol_binding", args)["ok"]
        b.skill("dbSave(holdSymbol)")
        assert not mcp_call(s, "get_symbol_binding", args)["ok"]
        b.skill(
            'progn(dbReplaceProp(holdSymbol "partName" "string" "guard") '
            "dbSave(holdSymbol) dbClose(holdSymbol))"
        )
        assert mcp_call(s, "get_symbol_binding", args) == before
        # Unsaved base CDF is local to this scratch design; no cdfSaveCDF needed.
        b.skill('guardCdf=cdfCreateBaseCellCDF(ddGetObj("genericWork" "guard"))')
        assert (
            b.skill(
                'cdfCreateParam(guardCdf ?name "gain" ?prompt "Gain" ?type "string" ?defValue "1")'
            )
            != "nil"
        )
        assert b.skill('length(cdfGetCellCDF(ddGetObj("genericWork" "guard"))~>parameters)') == "1"
        assert not mcp_call(s, "get_symbol_binding", args)["ok"]
        b.skill("cdfDeleteCDF(guardCdf)")
        assert mcp_call(s, "get_symbol_binding", args) == before
        scratch_schematic(b, "genericWork", "rectangle", local)
        rectangular = mcp_call(
            s,
            "preview_template_symbol",
            dict(
                library="genericWork",
                cell="rectangle",
                template_ref=source["template_ref"],
                task_ref=task["task_ref"],
                request_id="rectangle",
                net_scope_map=scopes,
            ),
        )
        rectangle = mcp_call(
            s,
            "create_template_symbol",
            dict(
                preview_ref=rectangular["preview_ref"],
                preview_digest=rectangular["preview_digest"],
                request_id="rectangle",
            ),
        )
        assert rectangle["ok"], rectangle
        rectangle_binding = mcp_call(
            s,
            "get_symbol_binding",
            dict(symbol_ref=rectangle["symbol_ref"], master_id="rectangle", instance_id="dut"),
        )
        assert rectangle_binding["ok"], rectangle_binding
        assert {p["anchors"][0]["escape"] for p in rectangle_binding["geometry"]["terminals"]} == {
            "left",
            "right",
        }
        (tmp_path / "binding.json").write_text(json.dumps(before, indent=2))
