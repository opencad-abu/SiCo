"""Recap footer stays scoped, bounded and independent of the message draft."""
# ruff: noqa: F401, F811 -- pytest fixture registration and injection

import time
from concurrent.futures import Future

import pytest
from agent_gui_wait import settle, wait_until
from agent_window_fixture import create_window as AssistantWindow
from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QColor
from PyQt5.QtTest import QTest
from sico.storage.journal import SessionJournal
from sico_ui.theme import active_palette
from test_codex_steering_gui import app  # noqa: F401
from test_codex_turn_inputs import backend


@pytest.fixture
def recap_window(tmp_path, app, monkeypatch):
    with SessionJournal(tmp_path, "recap-window") as journal:
        loop = backend(journal)
        loop._begin("检查接口", loop.state.context, "original", "chat")
        loop._event("model.completed", {"text": "接口检查完成。"})
        loop._finish("completed")
        window = AssistantWindow(loop)
        window.show()
        settle(window)
        panel = window.recap_panel
        panel.timer.stop()
        calls = []
        snapshot = {"supported": True, "busy": False, "text": "", "error": "",
                    "completed": 3, "new_completed": 3, "task_id": loop.state.task["id"]}
        original = window.api.command
        def command(token, name, *args, **kwargs):
            if not name.startswith("recap_"):
                return original(token, name, *args, **kwargs)
            calls.append((name, kwargs))
            future = Future()
            future.set_result(dict(snapshot))
            return future
        monkeypatch.setattr(window.api, "command", command)
        panel.tick()
        wait_until(lambda: panel.receipt.pending is None)
        try:
            yield window, snapshot, calls
        finally:
            window.close()
            assert window.sessions.wait(5)
            window.deleteLater()
            app.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()


@pytest.mark.parametrize("width", [800, 1280])
def test_footer_placement_chinese_text_and_draft(recap_window, tmp_path, width):
    window, snapshot, calls = recap_window
    panel = window.recap_panel
    window.resize(width, 800)
    window.input.setPlainText("保留我的草稿")
    snapshot["text"] = "已完成接口检查，当前符号与原理图端口一致；下一步建立测试平台。" * 5
    panel.button.click()
    wait_until(lambda: panel.receipt.pending is None)
    assert calls[-1][0] == "recap_start" and not calls[-1][1]["automatic"]
    assert window.input.toPlainText() == "保留我的草稿"
    assert panel.button.accessibleName() == "刷新纪要"
    assert panel.button.toolTip().startswith("刷新纪要：")
    assert panel.full_text == snapshot["text"]
    assert panel.label.text().startswith("已完成接口检查")
    assert "纪要 · " not in panel.label.text()
    assert f"border: 1px solid {active_palette().accent}" in panel.label.styleSheet()
    layout = window.input.parentWidget().layout()
    assert layout.indexOf(window.display) < layout.indexOf(panel) < layout.indexOf(window.input)
    assert panel.isVisible()
    from PyQt5.QtWidgets import QApplication

    QApplication.instance().processEvents()
    wait_until(lambda: panel.height() >= panel.label.height() + 8)
    assert panel.width() <= window.width() and panel.height() < 240
    assert window.grab().save(str(tmp_path / f"recap-{width}.png"))
    window.input.setPlainText("/recap")
    QTest.keyClick(window.input, Qt.Key_Return)
    wait_until(lambda: panel.receipt.pending is None and not window.input.toPlainText())
    assert calls[-1][0] == "recap_start"
    assert len([e for e in window.controller.journal.events() if e["kind"] == "task.started"]) == 1


def test_empty_recap_is_an_outlined_field_without_a_text_label(recap_window):
    window, _snapshot, _calls = recap_window
    panel = window.recap_panel
    assert panel.label.text() == ""
    assert panel.label.objectName() == "conversationRecapText"
    assert panel.label.isVisible()
    frame = panel.label.grab().toImage()
    accent = QColor(active_palette().accent)
    assert frame.pixelColor(0, frame.height() // 2) == accent


def test_action_button_height_tracks_recap_field(recap_window):
    window, snapshot, _calls = recap_window
    panel = window.recap_panel
    window.resize(1280, 800)
    settle(window)
    compact_height = panel.label.height()
    assert panel.button.height() == compact_height

    snapshot["text"] = "已完成接口检查，当前符号与原理图端口一致；下一步建立测试平台。" * 5
    panel.button.click()
    wait_until(lambda: panel.receipt.pending is None)
    settle(window)
    assert panel.label.height() > compact_height
    assert panel.button.height() == panel.label.height()

    window.resize(800, 800)
    settle(window)
    assert panel.button.height() == panel.label.height()
    assert panel.button.width() == window.turn_composer.settings.width()


def test_automatic_recap_only_after_away_idle_threshold(recap_window):
    window, snapshot, calls = recap_window
    panel = window.recap_panel
    window.hide()
    panel.tick()
    assert not any(name == "recap_start" for name, _ in calls)
    panel.away_since = panel.idle_since = time.monotonic() - 181
    panel.tick()
    wait_until(lambda: panel.receipt.pending is None)
    assert sum(name == "recap_start" for name, _ in calls) == 1
    assert calls[-1][1]["automatic"] is True
    panel.tick()
    assert sum(name == "recap_start" for name, _ in calls) == 1


def test_returning_from_another_page_preserves_away_time(recap_window):
    window, _snapshot, calls = recap_window
    panel = window.recap_panel
    panel.idle_since = time.monotonic() - 190
    window.page.reviewing = "another-session"
    panel.tick()
    session = window.page.session
    fingerprint, idle, _away = panel.visits[session]
    panel.visits[session] = fingerprint, idle, time.monotonic() - 185
    window.page.reviewing = None
    panel.tick()
    wait_until(lambda: panel.receipt.pending is None)
    panel.tick()
    wait_until(lambda: panel.receipt.pending is None)
    assert calls[-1][0] == "recap_start" and calls[-1][1]["automatic"]
    panel.tick()
    assert sum(name == "recap_start" for name, _ in calls) == 1


@pytest.mark.parametrize("change", ["activation", "task", "closing"])
def test_late_result_does_not_replace_another_page_or_clear_draft(
        recap_window, monkeypatch, change):
    window, snapshot, _calls = recap_window
    panel = window.recap_panel
    future = Future()
    future.set_running_or_notify_cancel()
    monkeypatch.setattr(window.api, "command", lambda *_a, **_kw: future)
    window.input.setPlainText("新草稿")
    panel.request()
    if change == "activation":
        window.page.activation += 1
    elif change == "task":
        window.presentation.state = {
            **window.presentation.state, "task": {"id": "new", "status": "completed"}}
    else:
        window.lifecycle.request()
    future.set_result({**snapshot, "text": "过期纪要"})
    panel.receipt.poll()
    assert "过期纪要" not in panel.label.text()
    assert window.input.toPlainText() == "新草稿"
