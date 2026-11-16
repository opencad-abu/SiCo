"""Main-window extension points, floating panels, layout restore and queue feedback."""

from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent_gui_wait import settle
from info_app_fixtures import notice_text
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QLabel, QMainWindow

from sico.core.contracts import BoundContext, ModelTurn, TextDelta
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.providers.mock import ScriptedProvider
from sico.storage.journal import SessionJournal
from agent_window_fixture import create_window as AssistantWindow


def pump(app, condition):
    deadline = time.monotonic() + 5
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert condition()
    app.processEvents()


def test_dock_layout_restore_and_module_registration_do_not_create_sessions(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    monkeypatch.setattr(app, "quit", lambda: None)  # Reopen within this test's shared Qt process.
    with SessionJournal(tmp_path, "workspace") as journal:
        loop = PythonBackend(
            ScriptedProvider([]), ToolRegistry(), journal, BoundContext("i", "g", "bound", {})
        )
        loop._event("session.created", {"entry": "test"})
        before = journal.events()
        window = AssistantWindow(loop)
        settle(window)
        assert isinstance(window, QMainWindow)
        window.add_workspace("test_report", "测试报告", QLabel("Local report fixture"))
        window.add_panel(
            "test_audit", "测试审计", QLabel("Local audit fixture"), Qt.LeftDockWidgetArea
        )
        window.show()
        window.show_panel("tools")
        window.panels["right"].setFloating(True)
        window.panels["left"].hide()
        app.processEvents()
        window.input.setPlainText("未提交草稿")
        window.tabs.setCurrentWidget(window.pages["test_report"])
        window.activateWindow()
        app.processEvents()
        QTest.keyClick(window, Qt.Key_L, Qt.ControlModifier)
        app.processEvents()
        assert window.input.hasFocus()
        assert window.input.toPlainText() == "未提交草稿"
        window.grab().save(str(tmp_path / "main-window.png"))
        assert journal.events() == before
        window.close()
        app.processEvents()
        reopened = AssistantWindow(loop)
        reopened.show()
        app.processEvents()
        assert reopened.panels["right"].isFloating()
        assert not reopened.panels["left"].isVisible()
        reopened.reset_layout()
        app.processEvents()
        assert not reopened.panels["right"].isFloating()
        assert reopened.panels["left"].isVisible()
        assert reopened.panels["right"].isVisible()
        assert len(list((journal.root / "sessions").iterdir())) == 1
        reopened.close()
        assert (journal.root / "workspace.ini").stat().st_mode & 0o777 == 0o600


def test_manual_queue_is_visible_cancelled_and_resumed_with_history(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    entered, release = threading.Event(), threading.Event()

    class Provider:
        calls = 0

        def stream(self, *args):
            self.calls += 1
            if self.calls == 1:
                yield TextDelta("第一项任务开始")
                entered.set()
                assert release.wait(5)
                yield ModelTurn("第一项任务开始")
            else:
                yield TextDelta("第二项完成")
                yield ModelTurn("第二项完成")

    with SessionJournal(tmp_path, "queue-ui") as journal:
        loop = PythonBackend(
            Provider(), ToolRegistry(), journal, BoundContext("i", "g", "bound", {"valid": True})
        )
        loop._event("session.created", {"entry": "test"})
        window = AssistantWindow(loop, persistent=True)
        settle(window)
        window.show()
        try:
            window.input.setPlainText("第一条中文任务")
            window.actions.submit()
            settle(window, tasks=False)
            pump(app, entered.is_set)
            window.input.setPlainText("第二条中文任务")
            window.steering_input.queue_action.trigger()
            settle(window, tasks=False)
            window.binding.poll()
            assert window.task_panel.topLevelItemCount() == 2
            assert "排队中" in window.task_panel.topLevelItem(1).text(0)
            window.input.setPlainText("未提交草稿")
            window.renderer.cancel_task()
            settle(window, tasks=False)
            assert window.presentation.state["paused"]
            assert window.presentation.state["cancelling"]
            assert window.status.text() == "正在取消…"
            window.close()
            assert not window.isVisible()
            release.set()
            pump(app, lambda: not window.controller.busy)
            window.restore()
            # The restored page catches up through the worker batch queue, so the
            # cancelled row and the resumed queue arrive over several polls.
            pump(
                app,
                lambda: "已取消" in window.task_panel.topLevelItem(0).text(0)
                and window.resume.isEnabled(),
            )
            assert window.resume.isEnabled()
            assert window.input.toPlainText() == "未提交草稿"
            window.renderer.resume_queue()
            settle(window, tasks=False)
            pump(app, lambda: not window.controller.busy)
            window.binding.poll()
            assert "已完成" in window.task_panel.topLevelItem(1).text(0)
            assert "第二项完成" in window.display.toPlainText()
            window.grab().save(str(tmp_path / "queue-completed.png"))
        finally:
            release.set()
            window.controller.close()
            assert window.controller.wait(5)
            window.lifecycle.closing = True
            window.close()


def test_invalid_event_batch_does_not_prevent_session_exit(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    with SessionJournal(tmp_path, "invalid-events") as journal:
        loop = PythonBackend(
            ScriptedProvider([]), ToolRegistry(), journal, BoundContext("i", "g", "bound", {})
        )
        window = AssistantWindow(loop, persistent=True)
        settle(window)
        window.show()
        batch = window.controller.updates.read_updates()
        monkeypatch.setattr(
            window.controller.updates,
            "read_updates",
            lambda *a, **k: {
                **batch,
                "runtime_id": "retired-runtime",
            },
        )
        window.binding.poll()
        window.sessions.wait(5)
        pump(app, lambda: window.binding.failed)
        assert not window.controller.closing
        assert window.controller._cancel.is_set()
        assert not window.send.isEnabled()
        assert "事件身份" in notice_text(window)
        assert "事件身份" not in window.status.text()
        window.request_quit()
        pump(app, lambda: not window.isVisible())
        assert not window.isVisible()
        assert not window.binding.timer.isActive()
