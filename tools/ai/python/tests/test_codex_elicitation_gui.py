"""Real Qt forms and main-window worker commands for bound MCP interactions."""

from copy import deepcopy

import pytest
from agent_gui_wait import settle, wait_until
from agent_window_fixture import create_window as AssistantWindow
from info_app_fixtures import notice_text
from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QCheckBox, QLabel
from sico_ui.records import AuditPanel
from test_codex_elicitation import event, opened, running  # noqa: F401
from test_codex_elicitation_runtime import CONTENT
from test_codex_steering_gui import publish


@pytest.fixture(scope="session")
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    application.processEvents()


def fill(form):
    for key in ("label", "limit"):
        form.fields[key][2].setText(str(CONTENT[key]))
    for key in ("enabled", "mode"):
        widget = form.fields[key][2]
        widget.setCurrentIndex(widget.findData(CONTENT[key]))
    for choice in form.fields["points"][2].findChildren(QCheckBox):
        choice.setChecked(choice.property("value") in CONTENT["points"])


@pytest.mark.parametrize("width", [360, 800])
def test_form_controls_validation_drafts_read_only_and_clear(running, app, tmp_path, width):  # noqa: F811
    backend, _, _ = running
    row = opened(backend)
    panel = AuditPanel("暂无交互")
    panel.resize(width, 620)
    panel.show()
    replies = []
    panel.elicitationRequested.connect(lambda row, result: replies.append(result))
    try:
        item = panel.add_audit(deepcopy(row))
        form = panel.elicitation_form
        form.submit("accept")
        assert form.validation.isVisible() and not replies
        fill(form)
        form.fields["label"][2].setText("修改中的草稿")
        panel.select(item)
        assert form.fields["label"][2].text() == "修改中的草稿"
        fill(form)
        form.buttons[0].clicked.emit(True)
        assert replies == [{"action": "accept", "content": CONTENT}]
        app.processEvents()
        assert panel.width() <= width
        assert panel.editor_scroll.horizontalScrollBar().maximum() == 0
        assert panel.grab().save(str(tmp_path / f"form-{width}.png"))
        panel.editor_scroll.verticalScrollBar().setValue(
            panel.editor_scroll.verticalScrollBar().maximum())
        app.processEvents()
        assert panel.grab().save(str(tmp_path / f"form-buttons-{width}.png"))
        panel.read_only = True
        panel.select(item)
        form.submit("decline")
        assert len(replies) == 1 and not form.isEnabled()
        panel.read_only = False
        panel.select(item)
        panel.clear()
        form.submit("decline")
        assert len(replies) == 1 and form.row is None and not form.isEnabled()
    finally:
        panel.close()
        panel.deleteLater()
        app.sendPostedEvents(None, QEvent.DeferredDelete)


def test_return_in_a_form_field_submits_the_complete_answer(running, app):  # noqa: F811
    """回车等同于“提交答复”：没填齐先报错，填齐后直接提交，主操作带强调色。"""
    backend, _, _ = running
    row = opened(backend)
    panel = AuditPanel("暂无交互")
    panel.resize(420, 620)
    panel.show()
    replies = []
    panel.elicitationRequested.connect(lambda row, result: replies.append(result))
    try:
        panel.add_audit(deepcopy(row))
        form = panel.elicitation_form
        assert form.buttons[0].text() == "提交答复"
        assert form.buttons[0].property("bottomAction") is True
        assert all(button.property("bottomAction") is not True for button in form.buttons[1:])

        QTest.keyClick(form.fields["label"][2], Qt.Key_Return)
        assert form.validation.isVisible() and not replies

        fill(form)
        QTest.keyClick(form.fields["label"][2], Qt.Key_Return)
        assert replies == [{"action": "accept", "content": CONTENT}]
        app.processEvents()
    finally:
        panel.close()
        panel.deleteLater()
        app.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.fixture
def window(running, app):  # noqa: F811
    backend, controller, _ = running
    widget = AssistantWindow(controller)
    widget.show()
    try:
        settle(widget, tasks=False)
        yield widget
    finally:
        backend.runtime = None
        backend.audit.elicitations.end("test ended")
        backend.steering.end()
        controller._running = False
        widget.close()
        assert widget.sessions.wait(5)
        widget.deleteLater()
        app.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()


def show_request(window, request=None):
    row = opened(window.controller.loop, request)
    publish(window)
    settle(window, tasks=False)
    panel = window.centers.audit
    window.centers.tabs.setCurrentWidget(panel)
    wait_until(lambda: row["id"] in panel.audit_rows)
    panel.list.setCurrentItem(panel.list.topLevelItem(panel.list.topLevelItemCount() - 1))
    panel.select(panel.list.currentItem())
    assert panel.elicitation_form.row["id"] == row["id"]
    return row


@pytest.mark.parametrize("width", [980, 1500])
def test_main_window_submission_and_passive_history(window, tmp_path, width):
    backend = window.controller.loop
    row = show_request(window)
    window.resize(width, 850)
    form = window.centers.audit.elicitation_form
    fill(form)
    QApplication.instance().processEvents()
    assert window.centers.audit.editor_scroll.horizontalScrollBar().maximum() == 0
    for label in form.fields["points"][2].findChildren(QLabel):
        assert label.height() >= label.heightForWidth(label.width())
    assert window.grab().save(str(tmp_path / f"interaction-pending-{width}.png"))
    form.buttons[0].clicked.emit(True)
    wait_until(lambda: row["status"] == "answer_received")
    assert backend.audit.deliver() == 1
    publish(window)
    settle(window, tasks=False)
    assert not window.binding.failed and not window.controller.inbox.pending
    assert "revised" in window.display.toPlainText()
    assert "服务结果另行核对" in window.display.toPlainText()
    assert not form.isEnabled()
    assert window.width() <= width
    assert window.grab().save(str(tmp_path / f"interaction-history-{width}.png"))


@pytest.mark.parametrize("boundary", ["unchanged", "switch", "ended"])
def test_url_needs_explicit_click_and_drops_late_receipt(window, monkeypatch, boundary):
    show_request(window, event(mode="url", url="https://example.invalid/review",
                               elicitationId="url-proof"))
    launches, pending = [], []
    monkeypatch.setattr(QDesktopServices, "openUrl",
                        lambda url: launches.append(url.toString()) or True)
    original = window.actions.run_command
    def delay(*args, **kwargs):
        pending.append(kwargs)
    monkeypatch.setattr(window.actions, "run_command", delay)
    assert not launches
    window.centers.audit.elicitation_form.buttons[0].clicked.emit(True)
    assert len(pending) == 1 and not launches
    if boundary == "switch":
        window.page.activation += 1
    elif boundary == "ended":
        window.controller.loop.audit.elicitations.end("ended before browser receipt")
        publish(window)
        settle(window, tasks=False)
    pending[0]["settled"]("https://example.invalid/review", None)
    pending[0]["success"]("https://example.invalid/review")
    assert launches == (["https://example.invalid/review"] if boundary == "unchanged" else [])
    assert not window.elicitation_actions.pending
    monkeypatch.setattr(window.actions, "run_command", original)


def test_url_worker_revalidates_context_and_never_submits_on_open(window, monkeypatch):
    row = show_request(window, event(mode="url", url="https://example.invalid/review",
                                    elicitationId="url-proof"))
    launches = []
    monkeypatch.setattr(QDesktopServices, "openUrl",
                        lambda url: launches.append(url.toString()) or True)
    window.centers.audit.elicitation_form.buttons[0].clicked.emit(True)
    wait_until(lambda: bool(launches))
    assert row["status"] == "pending" and "reply" not in row
    assert window.controller.loop.audit.elicitations._pending()


def test_failed_url_open_displays_error_without_submitting_response(window, monkeypatch):
    row = show_request(window, event(mode="url", url="https://example.invalid/review",
                                    elicitationId="url-proof"))
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda _url: False)
    window.centers.audit.elicitation_form.buttons[0].clicked.emit(True)
    wait_until(lambda: "错误 · 交互未提交" in notice_text(window))
    assert "网页未能打开" in notice_text(window)
    assert row["status"] == "pending" and "reply" not in row
    assert not window.elicitation_actions.pending
