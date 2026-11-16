"""Work/Stage/Execution tree and task-detail projections."""

from __future__ import annotations

import json
import uuid

import pytest

from sico.core.contracts import BoundContext, ToolCall
from sico.core.tools import ToolRegistry, tool_message
from sico.providers.mock import ScriptedProvider
from sico.service.backend import create_backend
from sico.service.controller import SessionController
from sico.service.task_detail import TaskDetailContent
from sico.service.workbench import WorkbenchError
from sico.storage.journal import SessionJournal


def backend_for(journal):
    context = BoundContext(
        "instance",
        "generation",
        "source",
        {"cwd": str(journal.root.parents[2]),
         "cellview": {"lib": "L", "cell": "c", "view_name": "schematic"}},
    )
    return create_backend(ScriptedProvider([]), ToolRegistry(), journal, context)


def start_task(backend, text, input_id, origin="chat"):
    backend._begin(text, None, input_id, origin=origin)
    backend._event("task.started", {
        "text": text,
        "context": backend.state.context.record(),
        "input_id": input_id,
        "origin": origin,
    })


def stage(backend, work_title, stage_title, *, work_id="", stage_id="",
          objective=None, acceptance=None):
    args = {
        "work_title": work_title,
        "stage_title": stage_title,
        "objective": objective or "确认 " + stage_title,
        "acceptance": acceptance or "保留证据并说明缺失项",
    }
    if work_id:
        args["work_id"] = work_id
    if stage_id:
        args["stage_id"] = stage_id
    invocation = ToolCall(uuid.uuid4().hex, "set_copilot_stage", args)
    backend._event("tool.started", invocation.record())
    reply = backend.tools.execute(invocation, backend.state.context)
    message = tool_message(invocation, reply, backend.config.tool_result_chars,
                           backend.journal.artifact)
    backend._event("tool.finished", {
        "id": invocation.id,
        "name": invocation.name,
        "result": json.loads(message["content"]),
    })
    backend.workbench.index.sync()
    return reply.data


def plan(backend, work_title, stages, *, work_id="", note=""):
    args = {"work_title": work_title, "stages": stages, "note": note}
    if work_id:
        args["work_id"] = work_id
    invocation = ToolCall(uuid.uuid4().hex, "plan_copilot_stages", args)
    backend._event("tool.started", invocation.record())
    reply = backend.tools.execute(invocation, backend.state.context)
    message = tool_message(invocation, reply, backend.config.tool_result_chars,
                           backend.journal.artifact)
    backend._event("tool.finished", {
        "id": invocation.id,
        "name": invocation.name,
        "result": json.loads(message["content"]),
    })
    backend.workbench.index.sync()
    return reply


def finish(backend, text="已完成"):
    backend._event("model.completed", {"text": text, "tool_calls": []})
    backend._finish("completed", "")
    backend.workbench.index.sync()


def update_stage(backend, work_id, stage_id, status, **extra):
    invocation = ToolCall(
        uuid.uuid4().hex, "update_copilot_stage",
        {"work_id": work_id, "stage_id": stage_id, "status": status, **extra},
    )
    backend._event("tool.started", invocation.record())
    reply = backend.tools.execute(invocation, backend.state.context)
    message = tool_message(invocation, reply, backend.config.tool_result_chars,
                           backend.journal.artifact)
    backend._event("tool.finished", {
        "id": invocation.id,
        "name": invocation.name,
        "result": json.loads(message["content"]),
    })
    backend.workbench.index.sync()
    return reply


def test_task_tree_groups_work_stage_and_execution_and_renders_details(tmp_path):
    with SessionJournal(tmp_path, "tree") as journal:
        backend = backend_for(journal)
        start_task(backend, "设计一个 LDO", "input-1")
        first = stage(backend, "LDO 设计", "规格确认与数据准备")
        finish(backend, "规格已确认")

        start_task(backend, "继续 LDO", "input-2")
        stage(backend, "LDO 设计", "电路绘制与优化",
              work_id=first["work"]["id"])
        finish(backend, "电路已绘制")

        start_task(backend, "设计一个比较器", "input-3")
        stage(backend, "比较器设计", "输入对管检查")
        finish(backend, "输入对管已检查")

        controller = SessionController(backend)
        try:
            tree = controller.task_tree.snapshot()
            assert [row["title"] for row in tree["works"]] == ["LDO 设计", "比较器设计"]
            ldo = tree["works"][0]
            assert [row["title"] for row in ldo["stages"]] == [
                "规格确认与数据准备", "电路绘制与优化"]
            assert [row["text"] for row in ldo["stages"][0]["executions"]] == [
                "设计一个 LDO"]
            assert ldo["stages"][0]["executions"][0]["task_id"]
            assert tree["unassigned"] == []

            index = backend.workbench.index
            detail = TaskDetailContent(index)
            work = detail.prepare("work", first["work"]["id"])
            assert work["title"] == "LDO 设计"
            assert "规格确认与数据准备" in work["prefix"]
            stage_detail = detail.prepare("stage", first["stage"]["id"])
            assert stage_detail["title"] == "规格确认与数据准备"
            assert "保留证据并说明缺失项" in stage_detail["prefix"]
            task = detail.prepare("task", ldo["stages"][0]["executions"][0]["task_id"])
            assert task["title"] == "设计一个 LDO"
            assert "规格已确认" in task["markdown"]
            assert "任务详情" in task["prefix"]
        finally:
            controller.close()
            assert controller.wait(5)


def test_task_tree_keeps_unassigned_executions_visible(tmp_path):
    with SessionJournal(tmp_path, "plain") as journal:
        backend = backend_for(journal)
        start_task(backend, "普通查询", "input-1")
        finish(backend, "查询完成")
        controller = SessionController(backend)
        try:
            tree = controller.task_tree.snapshot()
            assert tree["works"] == []
            assert [row["text"] for row in tree["unassigned"]] == ["普通查询"]
        finally:
            controller.close()
            assert controller.wait(5)


def test_task_tree_hides_startup_and_thread_operations(tmp_path):
    with SessionJournal(tmp_path, "system-tasks") as journal:
        backend = backend_for(journal)
        start_task(backend, "你好，获取一下当前窗口的信息", "startup", origin="startup")
        finish(backend, "上下文已读取")
        start_task(backend, "创建 invx1", "input-1")
        stage(backend, "AnalogTest invx1", "创建与表征")
        finish(backend, "已完成")
        start_task(backend, "会话操作", "thread-op", origin="thread_operation")
        finish(backend, "操作完成")
        controller = SessionController(backend)
        try:
            tree = controller.task_tree.snapshot()
            assert tree["unassigned"] == []
            assert [row["title"] for row in tree["works"]] == ["AnalogTest invx1"]
            assert [
                execution["text"]
                for stage_row in tree["works"][0]["stages"]
                for execution in stage_row["executions"]
            ] == ["创建 invx1"]
        finally:
            controller.close()
            assert controller.wait(5)


def test_followup_task_inherits_previous_stage_for_display(tmp_path):
    with SessionJournal(tmp_path, "followup") as journal:
        backend = backend_for(journal)
        start_task(backend, "创建 invx1", "input-1")
        stage(backend, "AnalogTest invx1", "创建与表征")
        finish(backend, "第一轮完成")
        start_task(backend, "pdk数据已经更新，请继续任务", "input-2")
        finish(backend, "继续完成")
        controller = SessionController(backend)
        try:
            tree = controller.task_tree.snapshot()
            assert tree["unassigned"] == []
            stage_node = tree["works"][0]["stages"][0]
            assert [row["text"] for row in stage_node["executions"]] == [
                "创建 invx1", "pdk数据已经更新，请继续任务"]
            assert stage_node["executions"][0].get("inherited") is None
            assert stage_node["executions"][1]["inherited"] is True
        finally:
            controller.close()
            assert controller.wait(5)


def test_failed_task_without_final_reply_still_has_diagnostics(tmp_path):
    with SessionJournal(tmp_path, "failed") as journal:
        backend = backend_for(journal)
        start_task(backend, "失败的规格确认", "input-1")
        first = stage(backend, "LDO 设计", "规格确认")
        backend._finish("failed", "缺少规格数据")
        backend.workbench.index.sync()
        controller = SessionController(backend)
        try:
            tree = controller.task_tree.snapshot()
            assert tree["works"][0]["stages"][0]["status"] == "blocked"
            task_id = tree["works"][0]["stages"][0]["executions"][0]["task_id"]
            detail = TaskDetailContent(backend.workbench.index).prepare("task", task_id)
            assert "失败" in detail["prefix"]
            assert "缺少规格数据" in detail["prefix"]
            assert "尚未产生最终回复" in detail["markdown"]
            assert first["stage"]["id"]
        finally:
            controller.close()
            assert controller.wait(5)


def test_model_stage_status_is_recorded_and_rendered(tmp_path):
    with SessionJournal(tmp_path, "stage-status") as journal:
        backend = backend_for(journal)
        start_task(backend, "设计一个 LDO", "input-1")
        first = stage(backend, "LDO 设计", "规格确认")
        reply = update_stage(
            backend, first["work"]["id"], first["stage"]["id"], "blocked",
            reason="缺少 PDK 数据", blocker="等待 PDK", next_step="补充 PDK 数据",
        )
        assert reply.status == "ok"
        finish(backend, "规格确认")
        controller = SessionController(backend)
        try:
            stage_node = controller.task_tree.snapshot()["works"][0]["stages"][0]
            assert stage_node["status"] == "blocked"
            assert stage_node["status_source"] == "model"
            assert stage_node["reason"] == "缺少 PDK 数据"
            assert stage_node["blocker"] == "等待 PDK"
            assert stage_node["next_step"] == "补充 PDK 数据"
            assert stage_node["facts"]["blocked"] is False
            assert stage_node["conflict"] is False
            detail = TaskDetailContent(backend.workbench.index).prepare(
                "stage", first["stage"]["id"])
            assert "模型判断" in detail["prefix"]
            assert "缺少 PDK 数据" in detail["prefix"]
        finally:
            controller.close()
            assert controller.wait(5)


def test_stage_status_rejects_missing_reason_foreign_stage_and_accepted(tmp_path):
    with SessionJournal(tmp_path, "stage-invalid") as journal:
        backend = backend_for(journal)
        start_task(backend, "设计一个 LDO", "input-1")
        unselected = update_stage(backend, "w_missing", "s_missing", "active")
        assert unselected.status == "tool_error"
        assert "set_copilot_stage" in unselected.summary
        first = stage(backend, "LDO 设计", "规格确认")
        assert update_stage(
            backend, first["work"]["id"], first["stage"]["id"], "blocked"
        ).status == "tool_error"
        assert update_stage(
            backend, first["work"]["id"], first["stage"]["id"], "reported"
        ).status == "tool_error"
        second = stage(backend, "LDO 设计", "电路设置",
                       work_id=first["work"]["id"])
        foreign = update_stage(
            backend, first["work"]["id"], first["stage"]["id"], "active"
        )
        assert foreign.status == "tool_error"
        assert "set_copilot_stage" in foreign.summary
        with pytest.raises(WorkbenchError):
            backend.workbench.update_stage(
                {"work_id": second["work"]["id"], "stage_id": second["stage"]["id"],
                 "status": "accepted"},
                backend.state.context,
            )


def test_model_active_conflicts_with_failed_execution_fact(tmp_path):
    with SessionJournal(tmp_path, "stage-conflict") as journal:
        backend = backend_for(journal)
        start_task(backend, "第一次失败", "input-1")
        first = stage(backend, "LDO 设计", "电路设置")
        backend._finish("failed", "工具失败")
        backend.workbench.index.sync()

        start_task(backend, "继续执行", "input-2")
        stage(backend, "LDO 设计", "电路设置",
              work_id=first["work"]["id"], stage_id=first["stage"]["id"])
        assert update_stage(
            backend, first["work"]["id"], first["stage"]["id"], "active"
        ).status == "ok"
        finish(backend, "继续完成")
        controller = SessionController(backend)
        try:
            stage_node = controller.task_tree.snapshot()["works"][0]["stages"][0]
            assert stage_node["status"] == "active"
            assert stage_node["facts"]["blocked"] is True
            assert stage_node["conflict"] is True
        finally:
            controller.close()
            assert controller.wait(5)


def test_stage_plan_is_visible_before_execution_and_survives_revision(tmp_path):
    with SessionJournal(tmp_path, "stage-plan") as journal:
        backend = backend_for(journal)
        start_task(backend, "设计一个 LDO", "input-1")
        planned = plan(backend, "LDO 设计", [
            {"stage_title": "规格确认", "objective": "确认输入输出规格",
             "acceptance": "规格表与用户确认"},
            {"stage_title": "电路绘制", "objective": "建立原理图",
             "acceptance": "符号、连线与网表可读", "depends_on": ["规格确认"]},
        ], note="LDO 阶段计划").data
        assert planned["plan"]["revision"] == 1
        first, second = planned["plan"]["stages"]
        finish(backend)
        tree = SessionController(backend).task_tree.snapshot()
        work = tree["works"][0]
        assert work["title"] == "LDO 设计"
        assert work["status"] == "planned"
        assert [row["title"] for row in work["stages"]] == ["规格确认", "电路绘制"]
        assert [row["status"] for row in work["stages"]] == ["planned", "planned"]
        assert work["stages"][1]["depends_on"] == [first["id"]]
        assert all(not row["executions"] for row in work["stages"])
        detail = TaskDetailContent(backend.workbench.index).prepare("stage", second["id"])
        assert "阶段计划" in detail["prefix"]
        assert "计划版本" in detail["prefix"]
        assert "依赖阶段" in detail["prefix"] and "规格确认" in detail["prefix"]
        assert "阶段尚未开始" in detail["prefix"]

        start_task(backend, "开始规格确认", "input-2")
        stage(backend, "LDO 设计", "规格确认", work_id=planned["work"]["id"],
              stage_id=first["id"], objective=first["objective"],
              acceptance=first["acceptance"])
        finish(backend)
        selected = SessionController(backend).task_tree.snapshot()["works"][0]["stages"]
        assert selected[0]["status"] == "in_progress"
        assert selected[1]["status"] == "planned"

        start_task(backend, "补充阶段计划", "input-3")
        revised = plan(backend, "LDO 设计", [
            {"stage_title": "规格确认", "objective": "确认输入输出规格",
             "acceptance": "规格表与用户确认"},
            {"stage_title": "电路绘制", "objective": "建立原理图",
             "acceptance": "符号、连线与网表可读", "depends_on": ["规格确认"]},
            {"stage_title": "仿真验证", "objective": "验证 DC/AC 指标",
             "acceptance": "报告与证据可追溯", "depends_on": ["电路绘制"]},
        ], work_id=planned["work"]["id"], note="加入仿真验证").data
        assert revised["plan"]["revision"] == 2
        assert revised["plan"]["stages"][0]["id"] == first["id"]
        finish(backend)
        after = SessionController(backend).task_tree.snapshot()["works"][0]["stages"]
        assert [row["title"] for row in after] == ["规格确认", "电路绘制", "仿真验证"]
        assert after[0]["status"] == "in_progress"
        assert after[1]["status"] == "planned"
        assert after[2]["status"] == "planned"
        assert after[2]["plan_revision"] == 2

        from sico.storage.workbench import WorkbenchIndex
        replayed = WorkbenchIndex(journal).sync()
        assert replayed.plans[planned["work"]["id"]]["revision"] == 2
        assert len(replayed.plans[planned["work"]["id"]]["stages"]) == 3


def test_stage_plans_are_per_work_and_not_hardcoded(tmp_path):
    with SessionJournal(tmp_path, "stage-plan-works") as journal:
        backend = backend_for(journal)
        start_task(backend, "设计一个 LDO", "input-1")
        ldo = plan(backend, "LDO 设计", [
            {"stage_title": "规格确认", "objective": "确认规格", "acceptance": "规格表"},
            {"stage_title": "版图检查", "objective": "检查版图", "acceptance": "DRC 报告"},
        ]).data
        finish(backend)
        start_task(backend, "设计一个比较器", "input-2")
        comparator = plan(backend, "比较器设计", [
            {"stage_title": "输入对管检查", "objective": "检查匹配", "acceptance": "记录"},
            {"stage_title": "失调仿真", "objective": "仿真失调", "acceptance": "波形"},
        ]).data
        finish(backend)
        tree = SessionController(backend).task_tree.snapshot()
        assert [row["title"] for row in tree["works"]] == ["LDO 设计", "比较器设计"]
        assert [row["title"] for row in tree["works"][0]["stages"]] == [
            "规格确认", "版图检查"]
        assert [row["title"] for row in tree["works"][1]["stages"]] == [
            "输入对管检查", "失调仿真"]
        assert ldo["work"]["id"] != comparator["work"]["id"]
        assert all(row["status"] == "planned" for row in tree["works"][1]["stages"])


def test_stage_plan_accepts_a_readable_new_work_id(tmp_path):
    with SessionJournal(tmp_path, "stage-plan-readable-id") as journal:
        backend = backend_for(journal)
        start_task(backend, "采集 PDK 数据", "input-1")
        planned = plan(backend, "smic18mmrf PDK 数据采集", [
            {"stage_title": "采集目录", "objective": "抓取目录",
             "acceptance": "目录完整"},
            {"stage_title": "整理缺项", "objective": "汇总缺项",
             "acceptance": "缺项可查询", "depends_on": ["采集目录"]},
        ], work_id="pdk_data_smic18mmrf")
        assert planned.status == "ok"
        assert planned.data["work"]["id"] == "pdk_data_smic18mmrf"


def test_stage_plan_rejects_duplicate_titles_and_unknown_dependencies(tmp_path):
    with SessionJournal(tmp_path, "stage-plan-invalid") as journal:
        backend = backend_for(journal)
        start_task(backend, "设计一个 LDO", "input-1")
        duplicate = plan(backend, "LDO 设计", [
            {"stage_title": "规格确认", "objective": "a", "acceptance": "b"},
            {"stage_title": "规格确认", "objective": "c", "acceptance": "d"},
        ])
        assert duplicate.status == "tool_error"
        unknown = plan(backend, "LDO 设计", [
            {"stage_title": "规格确认", "objective": "a", "acceptance": "b",
             "depends_on": ["不存在"]},
        ])
        assert unknown.status == "tool_error"


def test_task_detail_has_locate_copy_report_links_and_token_usage(tmp_path):
    with SessionJournal(tmp_path, "task-detail-actions") as journal:
        backend = backend_for(journal)
        start_task(backend, "设计一个 LDO", "input-1")
        task_id = backend.state.task["id"]
        first = stage(backend, "LDO 设计", "规格确认")
        backend._event("token.usage", {
            "input_tokens": 11, "output_tokens": 3, "total_tokens": 14,
            "context_usage": None,
        })
        finish(backend, "规格已确认")
        detail = TaskDetailContent(backend.workbench.index).prepare("task", task_id)
        assert "locate-input:input-1" in detail["prefix"]
        assert "copy-request:" + task_id in detail["prefix"]
        assert "copy-result:" + task_id in detail["prefix"]
        assert "查看工程任务" in detail["prefix"]
        assert "查看阶段计划" in detail["prefix"]
        assert "词元" in detail["prefix"] and "合计 14" in detail["prefix"]
        assert first["stage"]["id"] in detail["prefix"]


def test_codex_child_tasks_are_bounded_and_show_blockers(tmp_path):
    with SessionJournal(tmp_path, "child-tasks") as journal:
        backend = backend_for(journal)
        start_task(backend, "父任务", "input-1")
        task_id = backend.state.task["id"]
        stage(backend, "父工程", "实现")
        for index in range(25):
            backend._event("codex.child", {
                "thread_id": "child-%02d" % index,
                "parent_thread_id": "parent",
                "name": "子任务 %02d" % index,
                "status": "completed",
                "tool": "spawnAgent",
            })
        backend._event("codex.child", {
            "thread_id": "child-failed",
            "parent_thread_id": "parent",
            "name": "失败的子任务",
            "status": "failed",
            "tool": "spawnAgent",
        })
        finish(backend, "父任务完成")
        tree = SessionController(backend).task_tree.snapshot()
        execution = tree["works"][0]["stages"][0]["executions"][0]
        assert len(execution["children"]) == 20
        assert execution["children"][0]["thread_id"] == "child-failed"
        detail = TaskDetailContent(backend.workbench.index).prepare("task", task_id)
        assert "子任务与卡点" in detail["prefix"]
        assert "失败的子任务" in detail["prefix"]
