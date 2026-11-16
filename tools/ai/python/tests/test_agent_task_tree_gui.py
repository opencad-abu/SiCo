"""Task queue tree rendering and node activation."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QTextEdit

from agent_gui_wait import settle, wait_until
from agent_window_fixture import create_window
from test_agent_task_tree import backend_for, finish, stage, start_task

from sico.storage.journal import SessionJournal
from sico_ui.transcript import Transcript
from sico_ui.tasks import TaskPanel


def execution(task_id, text, status="completed"):
    return {
        "id": "input-" + (task_id or text),
        "task_id": task_id,
        "text": text,
        "status": status,
        "origin": "chat",
        "diagnostic": "",
    }


def tree():
    return {
        "works": [{
            "id": "w1",
            "title": "设计一个 LDO",
            "status": "active",
            "stages": [{
                "id": "s1",
                "title": "规格确认与数据准备",
                "objective": "确认规格",
                "acceptance": "保留证据",
                "status": "active",
                "executions": [
                    execution("t1", "设计一个 LDO", "executing"),
                    execution("t2", "继续 LDO", "completed"),
                ],
            }],
        }, {
            "id": "w2",
            "title": "设计一个比较器",
            "status": "reported",
            "stages": [{
                "id": "s2",
                "title": "输入对管检查",
                "objective": "检查对管",
                "acceptance": "给出结论",
                "status": "reported",
                "executions": [execution("t3", "比较器检查")],
            }],
        }],
        "unassigned": [execution("", "临时查询", "queued")],
        "truncated": False,
    }


def test_task_panel_renders_work_stage_execution_tree_and_activates_nodes():
    app = QApplication.instance() or QApplication([])
    panel = TaskPanel()
    panel.resize(320, 640)
    panel.show()
    received = []
    panel.nodeActivated.connect(lambda kind, key: received.append((kind, key)))
    try:
        panel.update_tree(tree(), current_task_id="t1")
        app.processEvents()
        assert panel.topLevelItemCount() == 3
        ldo = panel.topLevelItem(0)
        assert "设计一个 LDO" in ldo.text(0)
        assert "进行中" in ldo.text(1)
        stage = ldo.child(0)
        assert "规格确认与数据准备" in stage.text(0)
        assert "2 次" in stage.text(0)
        assert stage.childCount() == 2
        assert ldo.isExpanded() and stage.isExpanded()
        assert not panel.topLevelItem(1).isExpanded()

        panel._activate(ldo)
        panel._activate(stage)
        panel._activate(stage.child(0))
        queued = panel.topLevelItem(2).child(0).child(0)
        panel._activate(queued)
        assert received == [
            ("work", "w1"),
            ("stage", "s1"),
            ("task", "t1"),
            ("queued", "input-临时查询"),
        ]
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()


def test_task_panel_collapses_single_execution_count_and_keeps_selection():
    app = QApplication.instance() or QApplication([])
    panel = TaskPanel()
    panel.resize(320, 640)
    panel.show()
    try:
        panel.update_tree(tree(), current_task_id="t1")
        second = panel.topLevelItem(1)
        assert " 次" not in second.child(0).text(0)
        panel.setCurrentItem(second)
        panel.update_tree(tree(), current_task_id="t3")
        app.processEvents()
        assert panel.currentItem() is not None
        assert panel.currentItem().data(0, 0x0100) == "work:w2"
        assert panel.topLevelItem(1).isExpanded()
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()


def test_task_panel_marks_model_stage_conflict():
    app = QApplication.instance() or QApplication([])
    panel = TaskPanel()
    panel.resize(320, 640)
    panel.show()
    try:
        panel.update_tree({
            "works": [{
                "id": "w1",
                "title": "设计一个 LDO",
                "status": "active",
                "stages": [{
                    "id": "s1",
                    "title": "电路设置",
                    "objective": "建立电路",
                    "acceptance": "工具通过",
                    "status": "active",
                    "status_source": "model",
                    "reason": "模型认为仍在推进",
                    "blocker": "工具失败",
                    "next_step": "补充数据后重试",
                    "facts": {"active": False, "blocked": True, "reported": False},
                    "conflict": True,
                    "executions": [execution("t1", "建立电路", "failed")],
                }],
            }],
            "unassigned": [],
            "truncated": False,
        }, current_task_id="t1")
        app.processEvents()
        stage = panel.topLevelItem(0).child(0)
        assert "冲突" in stage.text(1)
        assert "工具失败" in stage.toolTip(0)
        assert "补充数据后重试" in stage.toolTip(0)
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()


def test_task_panel_replaces_previous_session_tasks():
    app = QApplication.instance() or QApplication([])
    panel = TaskPanel()
    panel.resize(320, 640)
    panel.show()
    try:
        panel.update_tree({
            "works": [{
                "id": "wa", "title": "会话 A 的任务", "status": "active",
                "stages": [],
            }],
            "unassigned": [], "truncated": False,
        }, session_id="session-a")
        assert panel.topLevelItemCount() == 1
        assert "会话 A" in panel.topLevelItem(0).text(0)
        panel.update_tree({
            "works": [{
                "id": "wb", "title": "会话 B 的任务", "status": "planned",
                "stages": [],
            }],
            "unassigned": [], "truncated": False,
        }, session_id="session-b")
        app.processEvents()
        assert panel.topLevelItemCount() == 1
        assert "会话 B" in panel.topLevelItem(0).text(0)
        assert "会话 A" not in panel.topLevelItem(0).text(0)
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()


def test_task_panel_flat_list_hides_startup_and_thread_operations():
    app = QApplication.instance() or QApplication([])
    panel = TaskPanel()
    panel.resize(320, 640)
    try:
        context = {"snapshot": {}}
        panel.update_requests([
            {"id": "startup", "task_id": "s", "text": "初始化上下文",
             "status": "completed", "origin": "startup", "context": context},
            {"id": "user", "task_id": "u", "text": "创建 invx1",
             "status": "completed", "origin": "chat", "context": context},
            {"id": "thread", "task_id": "t", "text": "会话操作",
             "status": "completed", "origin": "thread_operation", "context": context},
        ])
        app.processEvents()
        assert panel.topLevelItemCount() == 1
        assert "创建 invx1" in panel.topLevelItem(0).text(0)
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()


def test_window_task_tree_opens_work_stage_and_task_details(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    with SessionJournal(tmp_path, "window-tree") as journal:
        backend = backend_for(journal)
        start_task(backend, "设计一个 LDO", "input-1")
        first = stage(backend, "LDO 设计", "规格确认与数据准备")
        finish(backend, "规格已确认")
        window = create_window(backend)
        settle(window)
        window.show()
        try:
            panel = window.task_panel
            assert panel.topLevelItemCount() == 1
            work_item = panel.topLevelItem(0)
            stage_item = work_item.child(0)
            execution_item = stage_item.child(0)
            task_id = execution_item.data(0, 0x0101)
            panel._activate(work_item)
            wait_until(lambda: window.detail.current == ("work", first["work"]["id"]))
            settle(window)
            assert "LDO 设计" in window.detail.document.toPlainText()
            panel._activate(stage_item)
            wait_until(lambda: window.detail.current == ("stage", first["stage"]["id"]))
            settle(window)
            text = window.detail.document.toPlainText()
            assert "规格确认与数据准备" in text
            assert "设计一个 LDO" in text
            assert "执行记录" in text
            panel._activate(execution_item)
            wait_until(lambda: window.detail.current == ("task", task_id))
            settle(window)
            assert "规格已确认" in window.detail.document.toPlainText()
        finally:
            window.controller.close()
            assert window.controller.wait(5)
            window.lifecycle.closing = True
            window.close()
            app.processEvents()


def test_task_panel_shows_planned_stage_without_execution():
    app = QApplication.instance() or QApplication([])
    panel = TaskPanel()
    panel.resize(320, 640)
    panel.show()
    received = []
    panel.nodeActivated.connect(lambda kind, target: received.append((kind, target)))
    try:
        panel.update_tree({
            "works": [{
                "id": "w1", "title": "LDO 设计", "status": "planned",
                "stages": [{
                    "id": "s1", "title": "规格确认",
                    "objective": "确认输入输出规格", "acceptance": "规格表",
                    "status": "planned", "status_source": "plan",
                    "plan_revision": 1, "order": 0, "depends_on": [],
                    "facts": {}, "conflict": False, "executions": [],
                }],
            }],
            "unassigned": [], "truncated": False,
        })
        app.processEvents()
        work = panel.topLevelItem(0)
        stage_item = work.child(0)
        assert "计划中" in work.text(1)
        assert "计划中" in stage_item.text(1)
        assert stage_item.childCount() == 0
        assert "计划版本：1" in stage_item.toolTip(0)
        panel._activate(stage_item)
        assert received == [("stage", "s1")]
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()


def test_task_panel_filters_and_exposes_accessible_text():
    app = QApplication.instance() or QApplication([])
    panel = TaskPanel()
    panel.resize(320, 640)
    panel.show()
    try:
        panel.update_tree({
            "works": [
                {"id": "w1", "title": "LDO 设计", "status": "planned",
                 "stages": [{"id": "s1", "title": "规格确认", "status": "planned",
                             "executions": []}]},
                {"id": "w2", "title": "比较器设计", "status": "planned",
                 "stages": [{"id": "s2", "title": "输入对管", "status": "planned",
                             "executions": []}]},
            ],
            "unassigned": [], "truncated": False,
        })
        app.processEvents()
        assert panel.topLevelItem(0).data(0, Qt.AccessibleTextRole) is not None
        panel.set_filter("比较器")
        assert panel.topLevelItem(0).isHidden()
        assert not panel.topLevelItem(1).isHidden()
        assert "比较器设计" in panel.topLevelItem(1).data(0, Qt.AccessibleTextRole)
        panel.set_filter("")
        assert not panel.topLevelItem(0).isHidden()
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()


def test_transcript_can_scroll_to_a_rendered_message():
    app = QApplication.instance() or QApplication([])
    transcript = Transcript()
    first = transcript.append("user", "定位到这条请求")
    transcript.append("assistant", "已收到")
    view = QTextEdit()
    transcript.render(view, force=True)
    try:
        assert transcript.scroll_to_message(view, first["message_id"])
        assert view.textCursor().position() >= 0
    finally:
        view.deleteLater()
        app.processEvents()


def test_task_panel_renders_codex_child_tasks_under_execution():
    app = QApplication.instance() or QApplication([])
    panel = TaskPanel()
    panel.resize(360, 640)
    panel.show()
    received = []
    panel.nodeActivated.connect(lambda kind, target: received.append((kind, target)))
    try:
        panel.update_tree({
            "works": [{
                "id": "w1", "title": "父工程", "status": "active",
                "stages": [{
                    "id": "s1", "title": "实现", "status": "active",
                    "executions": [{
                        "id": "input-1", "task_id": "t1", "text": "父任务",
                        "status": "executing", "origin": "chat", "diagnostic": "",
                        "children": [
                            {"thread_id": "c1", "name": "失败子任务",
                             "status": "failed", "tool": "spawnAgent"},
                            {"thread_id": "c2", "name": "完成子任务",
                             "status": "completed", "tool": "spawnAgent"},
                        ],
                    }],
                }],
            }],
            "unassigned": [], "truncated": False,
        })
        app.processEvents()
        execution_item = panel.topLevelItem(0).child(0).child(0)
        assert execution_item.childCount() == 2
        child = execution_item.child(0)
        assert "失败" in child.text(1)
        panel._activate(child)
        assert received == [("task", "t1")]
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()
