"""Licensed approved edit, reroute and rebuild preservation contracts."""

import json
import os

import pytest
from circuit_create_fixture import fixture
from circuit_create_support import sample_master, session
from test_circuit_rc_gui import mcp_call


@pytest.mark.skipif(os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1", reason="licensed GUI acceptance")
def test_approved_in_place_edit_rebuilds_the_cellview(tmp_path):
    """Review, approve, then rewrite the cell in place; a stale review never writes."""
    with session(tmp_path) as (bridge, server, process):
        data = {c: sample_master(bridge, c) for c in ("res", "cap")}
        task = mcp_call(
            server,
            "begin_circuit_task",
            dict(task_id="edit", presentation="background", choice_confirmed=True),
        )
        def target_action(mode, name):
            observed = mcp_call(server, "inspect_circuit_target", {
                "target": {"library": "genericWork", "cell": "edited", "view": "schematic"},
                "task_ref": task["task_ref"], "request_id": name,
            })
            return observed

        # 1. create a stub-only sheet, as the live flow used to produce by default
        args = {
            **fixture(data, "edited", routing={"mode": "stub"}),
            "task_ref": task["task_ref"], "request_id": "seed",
        }
        prepared = mcp_call(server, "prepare_circuit_creation", args)
        assert prepared["ok"], prepared
        created = mcp_call(server, "create_circuit_from_plan", dict(
            prepare_ref=prepared["prepare_ref"],
            geometry_plan_digest=args["geometry_plan_digest"], request_id="seed"))
        assert created["ok"] and created["check"] == [0, 0], created
        stubs = json.loads(bridge.skill(
            'let((cv points) cv=dbOpenCellViewByType("genericWork" "edited" "schematic" "" "r") '
            'points=foreach(mapcar s setof(x cv~>shapes x~>objType=="line") s~>points) '
            'dbClose(cv) aiCircuitJson(points))'))
        assert all(len(points) == 2 for points in stubs), stubs

        # 2. the user reviews the edit plan before anything is written
        observed = target_action("inspect", "inspect_edit")
        assert "decision_required" not in observed, observed
        edit_args = {
            **fixture(data, "edited", routing={"mode": "end_to_end"}),
            "task_ref": task["task_ref"], "request_id": "edit",
            "target_ref": observed["target_ref"], "target_action": "edit",
            "edit": {"mode": "rebuild"},
        }
        reviewed = mcp_call(server, "prepare_circuit_edit", edit_args)
        assert reviewed["ok"] and reviewed["stage"] == "edit_review", reviewed
        assert reviewed["user_input_required"] and reviewed["selection_question"]["id"] == "circuit_edit"
        assert reviewed["review"]["instances"] and reviewed["review"]["wires"]["connections"] >= 1

        # A design that changed after the review is refused before the first deletion.
        bridge.skill('let((v) v=dbOpenCellViewByType("genericWork" "edited" "schematic" "" "a") '
                     'dbReplaceProp(v "user_edit" "string" "tamper") dbSave(v) dbClose(v))')
        blocked = mcp_call(server, "execute_circuit_edit", dict(
            prepare_ref=reviewed["prepare_ref"], request_id="edit",
            geometry_plan_digest=edit_args["geometry_plan_digest"],
            approval_digest=reviewed["review_digest"]))
        assert not blocked["ok"], blocked
        bridge.skill('let((v) v=dbOpenCellViewByType("genericWork" "edited" "schematic" "" "a") '
                     'dbReplaceProp(v "user_edit" "string" "") dbSave(v) dbClose(v))')

        # 3. a review that still matches the cellview runs after the user approves
        observed = target_action("inspect", "inspect_edit2")
        edit_args["target_ref"] = observed["target_ref"]
        edit_args["request_id"] = "edit2"
        reviewed = mcp_call(server, "prepare_circuit_edit", edit_args)
        assert reviewed["ok"], reviewed
        without_approval = mcp_call(server, "execute_circuit_edit", dict(
            prepare_ref=reviewed["prepare_ref"], request_id="edit2",
            geometry_plan_digest=edit_args["geometry_plan_digest"],
            approval_digest="0" * 64))
        assert not without_approval["ok"] and without_approval["code"] == "edit_approval_required"
        edited = mcp_call(server, "execute_circuit_edit", dict(
            prepare_ref=reviewed["prepare_ref"], request_id="edit2",
            geometry_plan_digest=edit_args["geometry_plan_digest"],
            approval_digest=reviewed["review_digest"]))
        assert edited["ok"] and edited["saved"] and edited["readback_verified"], edited
        assert edited["check"] == [0, 0] and edited["edited"] and edited["user_approved"]
        # The reviewed view is kept as a view-level backup inside the same cell.
        backup = edited["backup_target"]
        assert backup and backup[2].startswith("schematic_"), edited
        assert bridge.skill(
            f'let((b) b=dbOpenCellViewByType("genericWork" "edited" "{backup[2]}" "" "r") '
            'n=length(b~>instances) dbClose(b) n)') == "5"  # 2 devices + 3 pin instances
        wires = json.loads(bridge.skill(
            'let((cv points) cv=dbOpenCellViewByType("genericWork" "edited" "schematic" "" "r") '
            'points=foreach(mapcar s setof(x cv~>shapes x~>objType=="line") s~>points) '
            'dbClose(cv) aiCircuitJson(points))'))
        assert [[-2.0625, -1.0], [0.0, 0.0]] not in wires  # IN still sits off the pin row
        assert [[-2.0625, -1.0], [-1.375, -1.0]] in wires  # but it is a real route now


@pytest.mark.skipif(os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1", reason="licensed GUI acceptance")
def test_reroute_one_net_keeps_every_other_wire_and_device(tmp_path):
    """微调: 只重画 IN，器件/端口/其它网络的线一根都不动。"""
    with session(tmp_path) as (bridge, server, process):
        data = {c: sample_master(bridge, c) for c in ("res", "cap")}
        task = mcp_call(
            server, "begin_circuit_task",
            dict(task_id="reroute", presentation="background", choice_confirmed=True))
        args = {**fixture(data, "rerouted", routing={"mode": "stub"}),
                "task_ref": task["task_ref"], "request_id": "reroute_seed"}
        prepared = mcp_call(server, "prepare_circuit_creation", args)
        created = mcp_call(server, "create_circuit_from_plan", dict(
            prepare_ref=prepared["prepare_ref"],
            geometry_plan_digest=args["geometry_plan_digest"], request_id="reroute_seed"))
        assert created["ok"] and created["check"] == [0, 0], created
        before = json.loads(bridge.skill(
            'let((cv rows wires) cv=dbOpenCellViewByType("genericWork" "rerouted" "schematic" "" "r") '
            'rows=foreach(mapcar i cv~>instances list(i~>name i~>xy i~>orient)) '
            'wires=foreach(mapcar s setof(x cv~>shapes x~>objType=="line") s~>points) dbClose(cv) '
            'aiCircuitJson(list(rows wires)))'))
        observed = mcp_call(server, "inspect_circuit_target", {
            "target": {"library": "genericWork", "cell": "rerouted", "view": "schematic"},
            "task_ref": task["task_ref"], "request_id": "reroute_inspect"})
        edit_args = {
            **fixture(data, "rerouted", routing={"mode": "end_to_end"}),
            "task_ref": task["task_ref"], "request_id": "reroute",
            "target_ref": observed["target_ref"], "target_action": "edit",
            "edit": {"mode": "reroute", "nets": ["IN"]},
        }
        reviewed = mcp_call(server, "prepare_circuit_edit", edit_args)
        assert reviewed["ok"] and reviewed["review"]["reroutes"] == ["IN"], reviewed
        edited = mcp_call(server, "execute_circuit_edit", dict(
            prepare_ref=reviewed["prepare_ref"], request_id="reroute",
            geometry_plan_digest=edit_args["geometry_plan_digest"],
            approval_digest=reviewed["review_digest"]))
        assert edited["ok"] and edited["check"] == [0, 0] and edited["saved"], edited
        after = json.loads(bridge.skill(
            'let((cv rows wires) cv=dbOpenCellViewByType("genericWork" "rerouted" "schematic" "" "r") '
            'rows=foreach(mapcar i cv~>instances list(i~>name i~>xy i~>orient)) '
            'wires=foreach(mapcar s setof(x cv~>shapes x~>objType=="line") s~>points) dbClose(cv) '
            'aiCircuitJson(list(rows wires)))'))
        assert after[0] == before[0], "器件被动了"          # 器件/端口原封不动
        old = {tuple(tuple(p) for p in wire) for wire in before[1]}
        new = {tuple(tuple(p) for p in wire) for wire in after[1]}
        kept = old & new
        assert kept, "其它网络的线应当保留"
        assert old - new, "IN 的旧线（含旧短桩）应当被替换"
        assert new - old, "IN 应当画出新的走线"
        assert len(new) > len(old), "重画后线数应增加（一条 net 变成多段）"


@pytest.mark.skipif(os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1", reason="licensed GUI acceptance")
def test_rebuild_refuses_to_drop_unlisted_objects(tmp_path):
    """重建必须先点名计划外对象；明确 drops 后才允许继续。"""
    with session(tmp_path) as (bridge, server, process):
        data = {c: sample_master(bridge, c) for c in ("res", "cap")}
        task = mcp_call(
            server, "begin_circuit_task",
            dict(task_id="loss", presentation="background", choice_confirmed=True))
        args = {**fixture(data, "lossy", routing={"mode": "stub"}),
                "task_ref": task["task_ref"], "request_id": "loss_seed"}
        prepared = mcp_call(server, "prepare_circuit_creation", args)
        created = mcp_call(server, "create_circuit_from_plan", dict(
            prepare_ref=prepared["prepare_ref"],
            geometry_plan_digest=args["geometry_plan_digest"], request_id="loss_seed"))
        assert created["ok"], created
        # 用户手工加了一个计划外的器件
        bridge.skill('let((cv m i) cv=dbOpenCellViewByType("genericWork" "lossy" "schematic" "" "a") '
                     'm=dbOpenCellViewByType("analogLib" "res" "symbol" "" "r") '
                     'i=dbCreateInst(cv m "EXTRA" 8.0:0.0 "R0" 1) dbClose(m) '
                     'dbSave(cv) dbClose(cv))')
        observed = mcp_call(server, "inspect_circuit_target", {
            "target": {"library": "genericWork", "cell": "lossy", "view": "schematic"},
            "task_ref": task["task_ref"], "request_id": "loss_inspect"})
        edit_args = {
            **fixture(data, "lossy", routing={"mode": "end_to_end"}),
            "task_ref": task["task_ref"], "request_id": "loss",
            "target_ref": observed["target_ref"], "target_action": "edit",
            "edit": {"mode": "rebuild"},
        }
        refused = mcp_call(server, "prepare_circuit_edit", edit_args)
        assert not refused["ok"] and "edit_loss_check" in json.dumps(refused), refused
        assert "EXTRA" in json.dumps(refused), refused
        edit_args["request_id"] = "loss2"
        edit_args["edit"] = {"mode": "rebuild", "drops": ["EXTRA (res)"]}
        reviewed = mcp_call(server, "prepare_circuit_edit", edit_args)
        assert reviewed["ok"], reviewed
        assert reviewed["review"]["drops"] == ["EXTRA (res)"]
        edited = mcp_call(server, "execute_circuit_edit", dict(
            prepare_ref=reviewed["prepare_ref"], request_id="loss2",
            geometry_plan_digest=edit_args["geometry_plan_digest"],
            approval_digest=reviewed["review_digest"]))
        assert edited["ok"] and edited["check"] == [0, 0], edited
