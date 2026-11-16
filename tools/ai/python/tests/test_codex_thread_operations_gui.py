"""Qt thread controls preserve drafts, receipts and native capability limits."""

import threading
from concurrent.futures import Future
from types import SimpleNamespace

import pytest
from agent_gui_wait import wait_until
from agent_window_fixture import create_window as AssistantWindow
from PyQt5.QtCore import QEvent
from PyQt5.QtWidgets import QApplication, QLabel, QWidget
from sico.storage.journal import SessionJournal
from sico_ui.form_styles import checkbox_stylesheet
from sico_ui.goal_controls import GoalDialog
from sico_ui.history_controls import HistoryDialog
from sico_ui.memory_controls import MemoryDialog
from sico_ui.theme import active_palette
from sico_ui.thread_controls import show_thread_controls
from test_codex_steering_gui import app as app
from test_codex_thread_operations import goal, wired


def snapshot():
    return {"scope": {"session_id": "original"}, "thread_id": "thread",
            "goal": goal(objective="Long goal " * 500),
            "busy": False, "history_mode": "paginated", "memory": {"verified": True,
            "effective": {"enabled": False, "generate": False, "use": False}}}


@pytest.fixture
def control_window(app):
    owner, calls = object(), []
    state = snapshot()
    window = QWidget()
    window.page, window.presentation, window.lifecycle = (SimpleNamespace() for _ in range(3))
    window.page.session, window.page.activation = owner, 1
    window.page.opening = False
    window.presentation.displayed_context, window.page.reviewing = "context", None
    window.lifecycle.closing = False

    def command(handle, name, *args, **kwargs):
        calls.append((handle, name, args, kwargs))
        future = Future()
        future.set_result(state if name == "thread_status" else "accepted")
        return future

    window.api = SimpleNamespace(owns=lambda handle: handle is owner, command=command)
    yield window, state, calls
    window.close()
    window.deleteLater()
    app.processEvents()


def open_dialog(window, app, kind):
    dialog = kind(window)
    dialog.show()
    wait_until(lambda: dialog.scope is not None)
    return dialog


@pytest.fixture
def goal_controls(control_window, app):
    window, state, calls = control_window
    dialog = open_dialog(window, app, GoalDialog)
    try:
        yield window, dialog, state, calls
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


@pytest.fixture
def history_controls(control_window, app):
    window, state, calls = control_window
    dialog = open_dialog(window, app, HistoryDialog)
    try:
        yield window, dialog, state, calls
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


@pytest.fixture
def memory_controls(control_window, app):
    window, state, calls = control_window
    dialog = open_dialog(window, app, MemoryDialog)
    try:
        yield window, dialog, state, calls
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("width", [400, 560])
def test_goal_controls_fit_and_preserve_draft(goal_controls, tmp_path, width):
    _window, dialog, state, calls = goal_controls
    dialog.setFixedSize(width, 460)
    QApplication.instance().processEvents()
    assert dialog.minimumSizeHint().width() <= width
    assert dialog.unlimited.text() == "不限制词元预算"
    assert any(label.text() == "词元预算" for label in dialog.findChildren(QLabel))
    assert dialog.budget.parentWidget() is dialog.unlimited.parentWidget()
    assert dialog.budget.parentWidget() is not dialog
    assert abs(dialog.budget.geometry().center().y()
               - dialog.unlimited.geometry().center().y()) <= 2
    assert active_palette().accent in dialog.budget.styleSheet()
    assert dialog.unlimited.styleSheet() == checkbox_stylesheet(active_palette())
    assert "QCheckBox::indicator" in dialog.unlimited.styleSheet()
    assert "QCheckBox {" not in dialog.unlimited.styleSheet()
    dialog.objective.setPlainText("User edited goal")
    dialog.budget.setValue(73)
    dialog.render(state)
    assert dialog.objective.toPlainText() == "User edited goal" and dialog.budget.value() == 73
    assert dialog.summary.height() <= 64
    for label in dialog.findChildren(QLabel):
        if label.isVisible():
            assert label.width() <= dialog.width()
    assert dialog.grab().save(str(tmp_path / f"goal-controls-{width}.png"))
    dialog.start.click()
    wait_until(lambda: any(c[1] == "thread_operation" for c in calls))
    operation = next(c for c in calls if c[1] == "thread_operation")
    assert operation[2] == ({"kind": "goal_start", "objective": "User edited goal",
                             "token_budget": 73},)
    assert operation[3]["scope"] == state["scope"] and operation[3]["request_id"]


def test_explicit_unlimited_budget_draft_and_restored_status(goal_controls, tmp_path):
    _window, dialog, state, calls = goal_controls
    assert not dialog.unlimited.isChecked()
    dialog.budget.setValue(73)
    dialog.unlimited.setChecked(True)
    assert not dialog.budget.isEnabled()
    dialog.render(state)
    assert dialog.unlimited.isChecked() and dialog.budget.value() == 73
    dialog.start.click()
    wait_until(lambda: any(c[1] == "thread_operation" for c in calls))
    operation = next(c for c in calls if c[1] == "thread_operation")
    assert operation[2][0]["token_budget"] is None
    dialog.scope = None
    dialog.render({**state, "goal": goal(tokenBudget=None)})
    assert dialog.unlimited.isChecked() and "不限额" in dialog.summary.text()
    dialog.resize(440, 460)
    QApplication.instance().processEvents()
    assert dialog.grab().save(str(tmp_path / "goal-unlimited.png"))
    dialog.unlimited.setChecked(False)
    assert dialog.budget.isEnabled() and dialog.budget.value() == 0


def test_memory_controls_apply_only_memory_settings(memory_controls, tmp_path):
    _window, dialog, state, calls = memory_controls
    assert dialog.summary.isHidden()
    assert not hasattr(dialog, "tabs")
    assert not hasattr(dialog, "rollback")
    assert not hasattr(dialog, "review_type")
    for flag in dialog.memory_flags.values():
        assert flag.styleSheet() == checkbox_stylesheet(active_palette())
        assert "QCheckBox::indicator" in flag.styleSheet()
    dialog.memory_flags["enabled"].setChecked(True)
    dialog.memory_flags["generate"].setChecked(True)
    dialog.memory_flags["use"].setChecked(True)
    dialog.render(state)
    assert dialog.memory_flags["use"].isChecked()
    assert dialog.grab().save(str(tmp_path / "memory-controls.png"))
    dialog.apply.click()
    wait_until(lambda: any(c[1] == "thread_operation" for c in calls))
    operation = next(c for c in calls if c[1] == "thread_operation")
    assert operation[2] == ({"kind": "memory", "settings": {
        "enabled": True, "generate": True, "use": True}},)


def test_history_controls_do_not_expose_goal_or_memory(history_controls):
    _window, dialog, state, _calls = history_controls
    assert not hasattr(dialog, "tabs")
    assert not hasattr(dialog, "objective")
    assert not hasattr(dialog, "memory_flags")
    assert dialog.review.text() == "开始代码审阅"
    dialog.render({**state, "history_mode": "legacy"})
    assert not dialog.rollback.isEnabled()
    assert "旧格式" in dialog.rollback_hint.text()


@pytest.mark.parametrize("boundary", ["session", "activation", "context", "closed"])
def test_late_receipt_cannot_mutate_changed_view(goal_controls, boundary):
    window, dialog, state, calls = goal_controls
    future = Future()
    future.set_running_or_notify_cancel()
    window.api.command = lambda *_a, **_k: future
    dialog.reload()
    dialog.objective.setPlainText("Preserved draft")
    if boundary == "session":
        window.page.session = object()
    elif boundary == "activation":
        window.page.activation += 1
    elif boundary == "context":
        window.presentation.displayed_context = "other"
    else:
        dialog.close()
    future.set_result({**state, "goal": None})
    dialog.receipt.poll()
    assert dialog.objective.toPlainText() == "Preserved draft"
    assert not any(c[1] == "thread_operation" for c in calls)


def test_uncertain_command_receipt_is_not_retried(history_controls):
    window, dialog, _state, calls = history_controls
    attempts = []

    def command(*args, **kwargs):
        attempts.append(args[1])
        future = Future()
        future.set_exception(RuntimeError("Lost acknowledgement"))
        return future

    window.api.command = command
    dialog.send({"kind": "fork"})
    dialog.receipt.poll()
    dialog.reload()
    dialog.receipt.poll()
    assert attempts == ["thread_operation", "thread_status"]
    assert "Lost acknowledgement" in dialog.hint.text()
    assert not any(button.isEnabled() for button in dialog.buttons)


def test_status_failure_stops_automatic_polling_but_allows_explicit_reload(history_controls):
    window, dialog, _state, _calls = history_controls
    attempts = []

    def failed(*args, **kwargs):
        attempts.append(args[1])
        future = Future()
        future.set_exception(RuntimeError("Native runtime disconnected"))
        return future

    window.api.command = failed
    dialog.timer.setInterval(1)
    wait_until(lambda: not dialog.timer.isActive())
    assert attempts == ["thread_status"]
    assert "Native runtime disconnected" in dialog.hint.text()
    for _ in range(10):
        QApplication.instance().processEvents()
    assert attempts == ["thread_status"]
    dialog.reload()
    dialog.receipt.poll()
    assert attempts == ["thread_status", "thread_status"]
    assert not dialog.timer.isActive()


def test_main_window_dispatches_goal_clear_through_worker(
    tmp_path, monkeypatch, app,
):
    entered, release = threading.Event(), threading.Event()
    qt_thread, callers = threading.get_ident(), []
    with SessionJournal(tmp_path, "thread-window") as journal:
        loop, _ = wired(journal, monkeypatch)
        loop.thread_ops.goals.capture(goal(status="paused"), source="test")
        loop.runtime.home = journal.root / "codex"

        def request(method, _params):
            callers.append((method, threading.get_ident()))
            if method == "thread/goal/get":
                return {"goal": loop.thread_ops.goals.value}
            if method == "config/read":
                return {"config": {"features": {"memories": False},
                    "memories": {"generate_memories": False, "use_memories": False}}}
            assert method == "thread/goal/clear"
            entered.set()
            assert release.wait(5)
            loop.thread_ops.goals.value = None
            return {}

        loop.runtime.rpc.request = request
        loop.runtime.rpc.poll_notifications = lambda: None
        window = AssistantWindow(loop)
        window.resize(1500, 850)
        controller = window.controller
        window.show()
        try:
            wait_until(lambda: not window.page.opening)
            assert [action.text() for action in window.settings_menu.actions()] == [
                "外观设置…", "智能体设置…", "Codex 特性…", "工程记忆…"]
            assert "会话历史与操作…" in [
                action.text() for action in window.session_menu.actions()]
            show_thread_controls(window, page="goal")
            dialog = window.thread_dialog
            assert isinstance(dialog, GoalDialog)
            wait_until(lambda: dialog.scope is not None)
            dialog.timer.stop()
            dialog.clear.click()
            wait_until(entered.is_set)
            assert controller._running and not dialog.start.isEnabled()
            assert all(thread != qt_thread for _method, thread in callers)
            release.set()
            wait_until(lambda: not controller._running)
            wait_until(lambda: dialog.receipt.pending is None)
            dialog.reload()
            wait_until(lambda: dialog.hint.text() == "操作已完成")
            assert controller._task["status"] == "completed"
            assert [method for method, _thread in callers].count("thread/goal/clear") == 1
            assert not controller.inbox.pending
            assert window.grab().save(str(tmp_path / "goal-controls-main-window.png"))
        finally:
            release.set()
            assert controller.wait(5)
            window.close()
            assert window.sessions.wait(5)
            window.deleteLater()
            app.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()


def test_settings_and_session_entries_use_separate_dialogs(control_window):
    window, _state, _calls = control_window
    show_thread_controls(window, page="memory")
    memory = window.thread_dialog
    try:
        wait_until(lambda: memory.scope is not None)
        assert isinstance(memory, MemoryDialog)
        assert memory.windowTitle() == "工程记忆"
        assert not hasattr(memory, "tabs")
    finally:
        memory.close()
    show_thread_controls(window, page="history")
    history = window.thread_dialog
    try:
        wait_until(lambda: history.scope is not None)
        assert isinstance(history, HistoryDialog)
        assert history.windowTitle() == "会话历史与操作"
        assert not hasattr(history, "tabs")
    finally:
        history.close()
