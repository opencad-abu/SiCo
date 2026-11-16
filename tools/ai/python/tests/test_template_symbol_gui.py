"""Licensed template symbol creation, readback, refusal and visual acceptance."""

import copy
import json
from pathlib import Path

from template_symbol_fixture import scratch_schematic
from template_symbol_fixture import symbol_session as _symbol_session
from test_circuit_rc_gui import mcp_call

symbol_session = _symbol_session


def test_template_symbol_native_closed_loop(symbol_session, tmp_path):
    server, bridge, lib, inverter, stress = symbol_session
    old_env = bridge.skill('schGetEnv("vicViewList")')
    source_before = bridge.skill(
        'let((s v) s=dbOpenCellViewByType("gsclib045" "INVX1" "schematic" "" "r") '
        "v=aiCircuitJson(aiTsSnapshot(s)) dbClose(s) v)"
    )
    evidence = []
    for mode in ("foreground", "background"):
        task = mcp_call(
            server,
            "begin_circuit_task",
            {"task_id": mode, "presentation": mode, "choice_confirmed": True},
        )
        for typ, record in (("INV", inverter), ("Stress", stress)):
            cell = typ + "_" + mode
            port_map = {"DATA<7:0>": "DIN<7:0>", "VINP": "INP"} if typ == "Stress" else {}
            target_record = copy.deepcopy(record)
            for port in target_record["topology"]["ports"]:
                port["name"] = port_map.get(port["name"], port["name"])
            scratch_schematic(bridge, lib, cell, target_record, actual=typ == "INV")
            args = {
                "library": lib,
                "cell": cell,
                "template_ref": record["template_ref"],
                "task_ref": task["task_ref"],
                "request_id": cell,
                "port_map": port_map,
            }
            window_count = bridge.skill("length(hiGetWindowList('all))")
            preview = mcp_call(server, "preview_template_symbol", args)
            assert preview["ok"] and not preview["saved"], preview
            assert bridge.skill(f'ddGetObj("{lib}" "{cell}" "symbol")') == "nil"
            assert mcp_call(server, "preview_template_symbol", args) == preview
            create_args = {
                "preview_ref": preview["preview_ref"],
                "preview_digest": preview["preview_digest"],
                "request_id": cell,
            }
            result = mcp_call(server, "create_template_symbol", create_args)
            assert result["ok"] and result["saved"] and result["readback_verified"], result
            assert result["interface_check"] == [0, 0]
            assert result["presentation"] == mode
            assert bridge.skill('schGetEnv("vicViewList")') == old_env
            assert mcp_call(server, "create_template_symbol", create_args) == result
            assert mcp_call(
                server, "inspect_template_symbol", {"symbol_ref": result["symbol_ref"]}
            )["ok"]
            exists = mcp_call(
                server, "create_template_symbol", {**create_args, "request_id": cell + "_exists"}
            )
            assert (
                not exists["ok"] and exists["stage"] == "preflight" and not exists["view_created"]
            )
            mismatch = mcp_call(
                server, "create_template_symbol", {**create_args, "preview_digest": "0" * 64}
            )
            assert mismatch["code"] == "symbol_preview_not_found"
            entry = "aiTemplateSymbols[" + json.dumps(result["symbol_ref"]) + "]"
            snapshot = json.loads(bridge.skill(f"aiCircuitJson({entry}['snapshot])"))
            assert len(snapshot[0]) == len(record["topology"]["ports"])
            shape_rows = [json.loads(s) for s in snapshot[1]]
            annotations = [s for s in shape_rows if s.get("labelType") == "ILLabel"]
            assert len(annotations) == len(record["topology"]["ports"])
            assert all(s["text"].startswith("cdsTerm(") for s in annotations)
            if mode == "background":
                assert bridge.skill("length(hiGetWindowList('all))") == window_count
                assert (
                    bridge.skill(
                        "length(setof(v dbGetOpenCellViews() "
                        f'aiCircuitCvMatches(v list("{lib}" "{cell}" "symbol"))))'
                    )
                    == "0"
                )
            else:
                png = str(tmp_path / (cell + ".png"))
                assert (
                    bridge.skill(
                        f"hiExportImage(?window {entry}['window] "
                        f"?fileName {json.dumps(png)} ?width 1400 ?height 950)"
                    )
                    == "t"
                )
                assert Path(png).stat().st_size > 5000
                if typ == "Stress":
                    # Inspect a real design instance, where fixed analog/NLP labels are evaluated.
                    bridge.skill(
                        f'let((u m i) u=dbOpenCellViewByType("{lib}" "SymbolUsage" '
                        f'"schematic" "schematic" "a") m=dbOpenCellViewByType("{lib}" '
                        f'"{cell}" "symbol" "" "r") '
                        f'i=schCreateInst(u m "I_{cell}_0" 0:0 "R0") '
                        'unless(i error("symbol instance failed")) '
                        "dbSave(u) dbClose(m) dbClose(u) t)"
                    )
                    bridge.skill(
                        f'usageWindow=deOpenCellView("{lib}" "SymbolUsage" '
                        '"schematic" "schematic" nil "r")'
                    )
                    usage_png = json.dumps(str(tmp_path / "symbol-instance.png"))
                    bridge.skill("hiZoomIn(usageWindow list(-1:-5 9:1))")
                    assert (
                        bridge.skill(
                            f"hiExportImage(?window usageWindow ?fileName {usage_png} "
                            "?width 1400 ?height 950)"
                        )
                        == "t"
                    )
            evidence.append({"preview": preview, "created": result, "snapshot": snapshot})
    assert (
        bridge.skill(
            'let((s v) s=dbOpenCellViewByType("gsclib045" "INVX1" "schematic" "" "r") '
            "v=aiCircuitJson(aiTsSnapshot(s)) dbClose(s) v)"
        )
        == source_before
    )
    # Runtime changed-geometry detection must not rely on the saved creation receipt.
    ref = evidence[-1]["created"]["symbol_ref"]
    bridge.skill(
        f'let((c) c=dbOpenCellViewByType("{lib}" "Stress_background" "symbol" "" "a") '
        'schCreateSymbolShape(c "line" "outline" list(0:0 1:1)) dbSave(c) dbClose(c) t)'
    )
    assert (
        mcp_call(server, "inspect_template_symbol", {"symbol_ref": ref})["code"] == "symbol_changed"
    )
    (tmp_path / "evidence.json").write_text(json.dumps(evidence, indent=2))


def test_template_symbol_native_refusals(symbol_session, tmp_path):
    server, bridge, lib, inverter, stress = symbol_session
    task = mcp_call(
        server,
        "begin_circuit_task",
        {"task_id": "refuse", "presentation": "background", "choice_confirmed": True},
    )
    args = {
        "library": lib,
        "cell": "Target",
        "template_ref": stress["template_ref"],
        "task_ref": task["task_ref"],
        "request_id": "preview",
    }
    assert (
        mcp_call(server, "preview_template_symbol", {**args, "task_ref": "task:missing"})["code"]
        == "task_not_found"
    )
    scratch_schematic(bridge, lib, "Target", stress)
    # A wrong global net fails even when name/direction/member count match.
    bridge.skill(
        f'let((c) c=dbOpenCellViewByType("{lib}" "Target" "schematic" "" "r") dbReopen(c "a") '
        'dbRenameNet(dbFindTermByName(c "VDD")~>net "OTHER!") dbSave(c) dbClose(c) t)'
    )
    assert not mcp_call(server, "preview_template_symbol", args)["ok"]
    assert bridge.skill(f'ddGetObj("{lib}" "Target" "symbol")') == "nil"
    bridge.skill(
        f'let((c) c=dbOpenCellViewByType("{lib}" "Target" "schematic" "" "r") dbReopen(c "a") '
        'dbRenameNet(dbFindTermByName(c "VDD")~>net "VDD!") dbSave(c) dbClose(c) t)'
    )
    preview = mcp_call(server, "preview_template_symbol", {**args, "request_id": "good"})
    assert preview["ok"], preview
    conflict = mcp_call(
        server, "preview_template_symbol", {**args, "request_id": "good", "cell": "Elsewhere"}
    )
    assert conflict["code"] == "request_conflict_or_limit"
    # Unsaved schematic edits are refused before symbol creation.
    bridge.skill(
        f'progn(held=dbOpenCellViewByType("{lib}" "Target" "schematic" "" "r") dbReopen(held "a"))'
    )
    bridge.skill('dbFindTermByName(held "VINP")~>direction="output"')
    create = {
        "preview_ref": preview["preview_ref"],
        "preview_digest": preview["preview_digest"],
        "request_id": "unsaved",
    }
    refused = mcp_call(server, "create_template_symbol", create)
    assert not refused["ok"] and not refused["view_created"] and refused["stage"] == "preflight"
    assert mcp_call(server, "create_template_symbol", create) == refused
    assert bridge.skill(f'ddGetObj("{lib}" "Target" "symbol")') == "nil"
    bridge.skill(
        'progn(dbFindTermByName(held "VINP")~>direction="input" dbSave(held) dbClose(held))'
    )
    # A different local net name also invalidates the retained interface snapshot.
    bridge.skill(
        f'let((c) c=dbOpenCellViewByType("{lib}" "Target" "schematic" "" "r") dbReopen(c "a") '
        'dbRenameNet(dbFindTermByName(c "VINP")~>net "changed") dbSave(c) dbClose(c) t)'
    )
    refused = mcp_call(server, "create_template_symbol", {**create, "request_id": "changed"})
    assert not refused["ok"] and not refused["view_created"]
    # Source-library writes are refused at preview, even with a new cell name.
    assert not mcp_call(
        server,
        "preview_template_symbol",
        {
            **args,
            "library": "gsclib045",
            "cell": "INVX1",
            "template_ref": inverter["template_ref"],
            "request_id": "source",
        },
    )["ok"]
    (tmp_path / "refusals.json").write_text(json.dumps(refused, indent=2))
