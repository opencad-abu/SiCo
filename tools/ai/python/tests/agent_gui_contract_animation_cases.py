# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""agent gui contract animation contracts."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent_gui_contract_support import COLLAPSED_ICON
from agent_window_fixture import create_window as AssistantWindow
from PyQt5.QtWidgets import QApplication, QTextBrowser

from sico.core.contracts import BoundContext
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.providers.mock import ContextDemoProvider
from sico.storage.journal import SessionJournal
from sico_ui.transcript import Transcript


def test_transcript_animates_running_activity_without_touching_text():
    transcript = Transcript(username="system-user")
    transcript.receive({
        "kind": "task.started",
        "payload": {"origin": "chat", "text": "检查当前设计"},
    })
    transcript.receive({
        "kind": "model.status",
        "payload": {"phase": "waiting_model", "label": "等待 LLM 响应", "turn": 1},
    })
    app = QApplication.instance() or QApplication([])
    view = QTextBrowser()
    transcript.render(view)
    assert transcript.messages[-1]["text"] == "等待 LLM 响应（第 1 轮）"

    assert transcript.advance_activity() is True
    assert transcript.messages[-1]["suffix"] == "."
    transcript.render(view, force=True)
    assert "等待 LLM 响应（第 1 轮）." in view.toPlainText()

    transcript.advance_activity()
    transcript.advance_activity()
    transcript.render(view, force=True)
    assert transcript.messages[-1]["suffix"] == "..."
    assert "等待 LLM 响应（第 1 轮）..." in view.toPlainText()

    transcript.settle_activity()
    transcript.render(view, force=True)
    shown = view.toPlainText()
    assert "等待 LLM 响应（第 1 轮）" in shown
    assert "..." not in shown
    assert transcript.messages[-1]["text"] == "等待 LLM 响应（第 1 轮）"


def test_transcript_animates_running_tools_and_settles_on_result():
    transcript = Transcript(username="system-user")
    transcript.receive({
        "kind": "task.started",
        "payload": {"origin": "chat", "text": "检查当前设计"},
    })
    transcript.receive({
        "kind": "tool.started",
        "payload": {"id": "call-1", "name": "read_current_window"},
    })
    running = transcript.messages[-1]
    assert running["role"] == "tools"
    assert running["text"] == "工具 ×1 · 正在执行 read_current_window"
    assert transcript.advance_activity() is True
    # 工具计数以小三角形逐个出现代替省略号。
    assert running["marks"] == 1
    assert running["suffix"] == ""
    transcript.advance_activity()
    transcript.advance_activity()
    assert running["marks"] == 3
    transcript.advance_activity()
    assert running["marks"] == 1

    transcript.receive({
        "kind": "tool.finished",
        "payload": {"id": "call-1", "name": "read_current_window",
                    "result": {"status": "ok", "summary": "已读取"}},
    })
    # 批次还在执行：计数继续动（真实会话里单个调用常常不到一秒）。
    assert running["marks"] > 0
    assert running["suffix"] == ""
    assert running["role"] == "tools"
    assert running["text"] == "工具 ×1 · 成功 1"
    transcript.receive({
        "kind": "model.completed",
        "payload": {"text": "读取完成。"},
    })
    assert running["marks"] == 0
    assert transcript.advance_activity() is False


def test_tool_counter_renders_triangles_one_by_one_and_keeps_the_last():
    transcript = Transcript(username="system-user")

    def receive(kind, payload):
        transcript.receive({"kind": kind, "task_id": "task", "payload": payload})

    receive("task.started", {"origin": "chat", "text": "查一下"})
    receive("tool.started", {"id": "call-1", "name": "lookup"})
    QApplication.instance() or QApplication([])
    view = QTextBrowser()
    counts = []
    for _step in range(len(Transcript.TOOL_MARKS)):
        transcript.advance_activity()
        transcript.render(view, force=True)
        counts.append(view.document().toHtml().count(COLLAPSED_ICON))
    # 三个小三角形逐个出现；批次结束后只留最后一个用于展开/收拢。
    assert counts == [1, 2, 3]
    receive("tool.finished", {"id": "call-1", "name": "lookup",
                              "result": {"status": "ok", "summary": "已读取"}})
    assert transcript.advance_activity() is True
    receive("model.completed", {"text": "结束。"})
    transcript.render(view, force=True)
    assert view.document().toHtml().count(COLLAPSED_ICON) == 1
    assert "正在执行" not in view.toPlainText()


def test_tool_counter_keeps_animating_across_quick_calls_in_one_batch():
    """真实会话里很多调用只有零点几秒，动效要覆盖整个批次而不是单个调用。"""
    transcript = Transcript(username="system-user")

    def receive(kind, payload):
        transcript.receive({"kind": kind, "task_id": "task", "payload": payload})

    receive("task.started", {"origin": "chat", "text": "跑几个短命令"})
    marks = []
    group = None
    for index in range(4):
        receive("tool.started", {"id": f"call-{index}", "name": "codex_command"})
        group = transcript.tools
        transcript.advance_activity()
        marks.append(group["marks"])
        receive("tool.finished", {"id": f"call-{index}", "name": "codex_command",
                                  "result": {"status": "ok", "summary": "done"}})
    # 调用之间的间隙不重置动效，三角形仍然逐个出现。
    assert marks == [1, 2, 3, 1]
    assert group["state"] == "running"
    receive("model.delta", {"text": "完成。"})
    # 批次结束：计数不再动，只留一个三角形。
    assert transcript.tools is None
    assert group["state"] == "done"
    assert group["marks"] == 0
    assert transcript.advance_activity() is False


def test_window_runs_the_activity_animation_only_while_work_is_in_flight(tmp_path):
    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "animation") as journal:
        loop = PythonBackend(
            ContextDemoProvider(), ToolRegistry(), journal,
            BoundContext("animation-instance", "animation-generation", "bound", {"valid": True}),
        )
        window = AssistantWindow(loop)
        try:
            window.show()
            app.processEvents()
            assert not window.activity_timer.isActive()
            # 动画跟随对话里的在途气泡/计数，不依赖会话快照的 busy 标记。
            window.presentation.transcript.receive({
                "kind": "task.started", "task_id": "task",
                "payload": {"origin": "chat", "text": "检查当前设计"},
            })
            window.presentation.transcript.receive({
                "kind": "tool.started", "task_id": "task",
                "payload": {"id": "call-1", "name": "lookup"},
            })
            window.renderer.flush()
            assert window.activity_timer.isActive()
            assert window.activity_timer.interval() == 450
            # 计时器每跳一次，计数上的小三角逐个出现。
            group = window.presentation.transcript.tools
            window.renderer._tick_activity()
            assert group["marks"] == 1
            window.renderer._tick_activity()
            assert group["marks"] == 2
            window.presentation.transcript.receive({
                "kind": "tool.finished", "task_id": "task",
                "payload": {"id": "call-1", "name": "lookup",
                            "result": {"status": "ok", "summary": "已读取"}},
            })
            window.renderer.flush()
            # 批次还在执行：动效继续（真实会话里单个调用常常不到一秒）。
            assert window.activity_timer.isActive()
            window.presentation.transcript.receive({
                "kind": "model.completed", "task_id": "task",
                "payload": {"text": "读取完成。"},
            })
            window.renderer.flush()
            assert not window.activity_timer.isActive()
            assert group["marks"] == 0
            # 批次结束后只剩展开/收拢三角形。
            assert window.display.document().toHtml().count(COLLAPSED_ICON) == 1
        finally:
            window.close()
