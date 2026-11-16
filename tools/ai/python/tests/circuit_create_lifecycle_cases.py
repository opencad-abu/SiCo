"""Licensed generic creation, testbench and existing-view lifecycle contracts."""

import json
import os
from pathlib import Path

import pytest
from circuit_create_fixture import fixture, prepare
from circuit_create_support import sample_master, session
from test_circuit_rc_gui import mcp_call

from cadai.circuit_geometry import preview_geometry
from cadai.circuit_spec_plan import preview_circuit


@pytest.mark.skipif(os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1", reason="licensed GUI acceptance")
def test_generic_create_native_lifecycle(tmp_path):
    with session(tmp_path) as (bridge, server, process):
        data = {c: sample_master(bridge, c) for c in ("res", "cap")}
        (tmp_path / "masters.json").write_text(json.dumps(data, indent=2))
        records = []
        for mode in ("foreground", "background"):
            task = mcp_call(
                server,
                "begin_circuit_task",
                dict(task_id=mode, presentation=mode, choice_confirmed=True),
            )
            args = {**fixture(data, mode), "task_ref": task["task_ref"], "request_id": mode}
            prepared = mcp_call(server, "prepare_circuit_creation", args)
            assert prepared["ok"] and prepared["stage"] == "prepared", prepared
            assert bridge.skill(f'ddGetObj("genericWork" "{mode}" "schematic")') == "nil"
            assert mcp_call(server, "prepare_circuit_creation", args) == prepared
            create_args = dict(
                prepare_ref=prepared["prepare_ref"],
                geometry_plan_digest=args["geometry_plan_digest"],
                request_id=mode,
            )
            created = mcp_call(server, "create_circuit_from_plan", create_args)
            assert created["ok"] and created["saved"] and created["readback_verified"], created
            assert created["check"] == [0, 0] and created["window_open"] == (mode == "foreground")
            svg = created["svg_preview"]
            assert svg["status"] == "ready" and svg["artifact"], svg
            assert Path(svg["artifact"]["path"]).read_text().startswith("<svg")
            assert svg["target"] == ["genericWork", mode, "schematic"]
            assert svg["acceptance_blocking"] is False
            assert created == mcp_call(server, "create_circuit_from_plan", create_args)
            assert mcp_call(
                server, "inspect_created_circuit", dict(circuit_ref=created["circuit_ref"])
            )["ok"]
            exists = mcp_call(
                server, "create_circuit_from_plan", {**create_args, "request_id": mode + "_exists"}
            )
            assert not exists["ok"] and not exists["view_created"]
            records.append(created)
            released = mcp_call(
                server, "release_created_circuit", dict(circuit_ref=created["circuit_ref"])
            )
            assert released["released"]
            assert released["window_open"] == (mode == "foreground")
            edit_args = prepare(server, data, "edit_" + mode, mode)
            editable = mcp_call(server, "create_circuit_from_plan", edit_args)
            assert editable["ok"]
            # User changes are rejected without schCheck/save or repeated creation.
            edit = (
                f'v=dbOpenCellViewByType("genericWork" "edit_{mode}" "schematic" "" "a") '
                'dbReplaceProp(v "userEdit" "string" "keep") '
            )
            bridge.skill(
                "let((v) " + edit + ("dbSave(v) " if mode == "background" else "") + "dbClose(v))"
            )
            changed = mcp_call(
                server, "inspect_created_circuit", dict(circuit_ref=editable["circuit_ref"])
            )
            assert changed["code"] == "created_circuit_changed"
            assert mcp_call(server, "create_circuit_from_plan", edit_args) == editable
            assert (
                mcp_call(
                    server, "release_created_circuit", dict(circuit_ref=editable["circuit_ref"])
                )["code"]
                == "created_circuit_changed"
            )
        # Change only in-memory base CDF in this isolated process; never cdfSaveCDF.
        # An effective CDF object alone is transient and does not change the source.
        stale = prepare(server, data, "stale_cdf")
        bridge.skill(
            'progn(testCdf=cdfGetBaseCellCDF(ddGetObj("analogLib" "res")) '
            'testParam=cdfFindParamByName(testCdf "r") testDefault=testParam~>defValue '
            'testParam~>defValue="77K")'
        )
        assert (
            bridge.skill(
                'cdfFindParamByName(cdfGetCellCDF(ddGetObj("analogLib" "res")) "r")~>defValue'
            )
            == "77K"
        )
        failed = mcp_call(server, "create_circuit_from_plan", stale)
        assert not failed["ok"] and not failed["view_created"] and failed["stage"] == "preflight"
        assert bridge.skill('ddGetObj("genericWork" "stale_cdf" "schematic")') == "nil"
        bridge.skill("testParam~>defValue=testDefault")
        assert mcp_call(server, "create_circuit_from_plan", stale) == failed
        # A callback added after preparation must be refused without running it.
        changed_hooks = prepare(server, data, "changed_hooks")
        bridge.skill(
            "progn(testOldCallback=testParam~>callback testCallbackRan=nil "
            'testParam~>callback="testCallbackRan=t")'
        )
        rejected = mcp_call(server, "create_circuit_from_plan", changed_hooks)
        assert not rejected["view_created"]
        assert bridge.skill("testCallbackRan") == "nil"
        # CDF's setter requires a string; empty string restores absent callback behavior.
        bridge.skill('testParam~>callback=or(testOldCallback "")')
        # Inject a build failure only into the isolated harness, then restore the real backend.
        partial = prepare(server, data, "partial", "background")
        bridge.skill(
            "progn(testBuildOriginal=getd('aiCrBuild) "
            'procedure(aiCrBuild(cv entry) schCreateNoteLabel(cv 0:0 "partial" '
            '"lowerLeft" "R0" "fixed" 0.125 "normalLabel") error("injected build failure")))'
        )
        failed = mcp_call(server, "create_circuit_from_plan", partial)
        assert failed["view_created"] and not failed["saved"] and not failed["ok"]
        bridge.skill("putd('aiCrBuild testBuildOriginal)")
        assert mcp_call(server, "create_circuit_from_plan", partial) == failed
        assert (
            mcp_call(server, "release_created_circuit", dict(circuit_ref=failed["circuit_ref"]))[
                "code"
            ]
            == "partial_circuit_retained"
        )
        assert (
            bridge.skill('dbIsCellViewModified(aiCreatedCircuits["created:partial"][\'partialCv])')
            == "t"
        )
        final = {c: sample_master(bridge, c) for c in ("res", "cap")}
        for samples in (data, final):
            for captured in samples.values():
                for parameter in captured[5]:
                    parameter[3] = parameter[3] or ""
        assert final == data
        (tmp_path / "creation.json").write_text(json.dumps(records, indent=2))


@pytest.mark.skipif(os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1", reason="licensed GUI acceptance")
def test_generic_create_design_instance_tb(tmp_path):
    from circuit_create_tb_fixture import tb_fixture

    with session(tmp_path) as (bridge, server, process):
        task = mcp_call(
            server,
            "begin_circuit_task",
            dict(task_id="tb", presentation="foreground", choice_confirmed=True),
        )
        args = {
            **tb_fixture(bridge, "tb", tmp_path),
            "task_ref": task["task_ref"],
            "request_id": "tb",
        }
        prepared = mcp_call(server, "prepare_circuit_creation", args)
        assert prepared["ok"], prepared
        created = mcp_call(
            server,
            "create_circuit_from_plan",
            dict(
                prepare_ref=prepared["prepare_ref"],
                geometry_plan_digest=args["geometry_plan_digest"],
                request_id="tb",
            ),
        )
        (tmp_path / "tb-result.json").write_text(json.dumps(created, indent=2))
        if not created["ok"]:
            (tmp_path / "tb-dependencies.json").write_text(bridge.skill(
                'let((e) e=aiCreatedCircuits["created:tb"] '
                'aiCircuitJson(list(e[\'dependencies] aiCrDependencies(e))))'))
        assert created["ok"] and created["readback_verified"], json.dumps(created)
        assert created["check"] == [0, 0] and created["window_open"]
        name = bridge.skill(
            'let((v r) v=dbOpenCellViewByType("genericWork" "tb" "schematic" "" "r") '
            'r=dbFindAnyInstByName(v "I_passive_dut_0")~>cellName dbClose(v) r)'
        )
        assert name == "passive_dut"
        assert mcp_call(
            server, "inspect_created_circuit", dict(circuit_ref=created["circuit_ref"])
        )["ok"]
        assert mcp_call(
            server, "release_created_circuit", dict(circuit_ref=created["circuit_ref"])
        )["window_open"]
        (tmp_path / "tb-creation.json").write_text(json.dumps(created, indent=2))


@pytest.mark.skipif(os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1", reason="licensed GUI acceptance")
def test_existing_schematic_reuse_continue_replace_and_stale_content(tmp_path):
    with session(tmp_path) as (bridge, server, _):
        data = {c: sample_master(bridge, c) for c in ("res", "cap")}
        for mode in ("foreground", "background"):
            mcp_call(server, "begin_circuit_task", dict(task_id=mode, presentation=mode, choice_confirmed=True))
            cell = "existing_" + mode
            if mode == "foreground":
                bridge.skill(f'progn(targetWindow=deNewCellView("genericWork" "{cell}" "schematic" "schematic" nil) '
                             'aiCopilotSource=makeTable("source" nil) aiCopilotSource[\'window]=targetWindow)')
            else:
                bridge.skill(f'progn(aiCopilotSource=nil targetCv=dbOpenCellViewByType("genericWork" "{cell}" '
                             '"schematic" "schematic" "a") dbSave(targetCv) dbClose(targetCv))')

            def inspect(request):
                value = mcp_call(server, "inspect_circuit_target", {
                    "target": {"library": "genericWork", "cell": cell, "view": "schematic"},
                    "task_ref": "task:" + mode, "request_id": request + mode})
                assert value["ok"], value
                return value

            def prepare_existing(args, observed, action, request):
                value = mcp_call(server, "prepare_circuit_creation", {
                    **args, "task_ref": "task:" + mode, "request_id": request + mode,
                    "target_ref": observed["target_ref"], "target_action": action})
                return value, {"prepare_ref": value.get("prepare_ref", ""),
                               "geometry_plan_digest": args["geometry_plan_digest"], "request_id": request + mode}

            empty = inspect("empty")
            assert empty["content_state"] == "empty" and not empty.get("decision_required")
            args = fixture(data, cell)
            prepared, execute = prepare_existing(args, empty, "reuse_empty", "empty")
            assert prepared["ok"], prepared
            created = mcp_call(server, "create_circuit_from_plan", execute)
            assert created["ok"] and created["saved"] and created["readback_verified"], created
            assert created["view_reused"] and not created["view_created"]
            assert created["window_open"] == (mode == "foreground")
            assert mcp_call(server, "create_circuit_from_plan", execute) == created

            observed = inspect("continue")
            assert "decision_required" not in observed
            assert len(observed["contents"]["instances"]) == 5
            assert next(i for i in observed["contents"]["instances"] if i["name"] == "RF")["connections"]
            conflict, _ = prepare_existing(args, observed, "continue", "conflict")
            assert not conflict["ok"] and "name conflicts" in conflict["message"]
            addition = fixture(data, cell)
            for inst in addition["spec"]["instances"]:
                inst["name"] += "2"
                inst["connections"] = {k: v + "2" for k, v in inst["connections"].items()}
            for net in addition["spec"]["nets"]:
                net["name"] += "2"
            for port in addition["spec"]["ports"]:
                port["name"] += "2"
                port["net"] += "2"
            addition["layout"]["ports"] = {k + "2": v for k, v in addition["layout"]["ports"].items()}
            addition["preview_digest"] = preview_circuit(addition["spec"], addition["bindings"])["preview_digest"]
            addition.pop("geometry_plan_digest")
            addition["geometry_plan_digest"] = preview_geometry(**addition)["geometry_plan_digest"]
            collision, _ = prepare_existing(addition, observed, "continue", "collision")
            assert not collision["ok"] and "overlaps" in collision["message"]
            for item in list(addition["layout"]["ports"].values()) + list(addition["layout"]["instances"].values()):
                item["xy"][1] += 10
            addition.pop("geometry_plan_digest")
            addition["geometry_plan_digest"] = preview_geometry(**addition)["geometry_plan_digest"]
            prepared, execute = prepare_existing(addition, observed, "continue", "continue")
            assert prepared["ok"], prepared
            created = mcp_call(server, "create_circuit_from_plan", execute)
            if not created["ok"]:
                (tmp_path / "continue-snapshots.json").write_text(bridge.skill(
                    f'let((e v) e=aiCreatedCircuits["created:continue{mode}"] '
                    f'v=dbOpenCellViewByType("genericWork" "{cell}" "schematic" "" "r") '
                    'aiCircuitJson(list(e[\'baseSnapshot] aiCrSnapshot(v))))'))
            assert created["ok"] and created["readback_verified"], json.dumps(created)
            assert len(inspect("combined")["contents"]["instances"]) == 10

            connected = fixture(data, cell)
            for inst in connected["spec"]["instances"]:
                inst["name"] += "3"
            connected["spec"]["ports"] = []
            connected["layout"]["ports"] = {}
            for item in connected["layout"]["instances"].values():
                item["xy"][1] += 20
            connected["preview_digest"] = preview_circuit(connected["spec"], connected["bindings"])["preview_digest"]
            connected.pop("geometry_plan_digest")
            connected["geometry_plan_digest"] = preview_geometry(**connected)["geometry_plan_digest"]
            observed = inspect("connect")
            prepared, execute = prepare_existing(connected, observed, "continue", "connect")
            assert prepared["ok"], prepared
            created = mcp_call(server, "create_circuit_from_plan", execute)
            assert created["ok"] and created["readback_verified"], created
            combined = inspect("connected")["contents"]
            assert len(combined["instances"]) == 12 and len(combined["ports"]) == 6
            for name in ("RF", "RF3"):
                connections = next(i for i in combined["instances"] if i["name"] == name)["connections"]
                assert {row[0]: row[1] for row in connections} == {"PLUS": "IN", "MINUS": "OUT"}

            observed = inspect("replace")
            prepared, execute = prepare_existing(args, observed, "replace", "replace")
            assert prepared["ok"], prepared
            replaced = mcp_call(server, "create_circuit_from_plan", execute)
            assert replaced["ok"] and replaced["saved"] and replaced["backup_target"], replaced
            assert len(inspect("replaced")["contents"]["instances"]) == 5
            backup_view = replaced["backup_target"][2]
            assert bridge.skill(f'let((b n) b=dbOpenCellViewByType("genericWork" "{cell}" "{backup_view}" "" "r") '
                                'n=length(b~>instances) dbClose(b) n)') == "12"

            stale = inspect("stale")
            prepared, execute = prepare_existing(args, stale, "replace", "stale")
            assert prepared["ok"]
            bridge.skill(f'let((s) s=dbOpenCellViewByType("genericWork" "{cell}" "schematic" "" "a") '
                         'dbReplaceProp(s "user_edit" "string" "retain") dbSave(s) dbClose(s))')
            blocked = mcp_call(server, "create_circuit_from_plan", execute)
            assert not blocked["ok"] and not blocked["view_reused"] and not blocked["backup_target"]
