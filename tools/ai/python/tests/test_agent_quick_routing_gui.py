"""Design routing receipts, page activation and drafts across the real Qt input."""

import threading

import pytest
from agent_gui_wait import settle, wait_until
from agent_quick_fixture import QuickInput
from agent_window_fixture import create_window as AssistantWindow
from info_app_fixtures import dismiss_notice, notice_text
from PyQt5.QtCore import QEvent, QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
from test_agent_quick_routing import desktop, message, target  # noqa: F401


@pytest.fixture
def window(desktop):  # noqa: F811
    app = QApplication.instance() or QApplication([])
    widget = AssistantWindow(desktop.controller, sessions=desktop.sessions)
    desktop.notices = []
    widget.quick_input = QuickInput(
        widget, desktop.broker, lambda kind, **data: desktop.notices.append((kind, data)),
    )
    widget.show()
    try:
        settle(widget, tasks=False)
        yield widget
    finally:
        widget.close()
        assert widget.sessions.wait(5)
        widget.deleteLater()
        app.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()


def test_unrelated_ask_opens_its_page_then_original_cell_returns_with_draft(window, desktop):  # noqa: F811
    original = window.controller
    window.input.setPlainText("Amp 草稿")
    source = target(desktop.context, "ask_tb", cell="TB", view="maestro_tran1")
    window.quick_input.accept(message(source))
    settle(window, tasks=False)
    other = window.controller
    assert other is not original and other.current == source
    assert other.inbox.pending[0]["message"]["context"] == source.record()
    assert window.input.toPlainText() == ""
    assert window.drafts.load(original)[0] == "Amp 草稿"
    window.input.setPlainText("TB 草稿")
    variant = target(source, "ask_tb2", cell="TB", view="maestro_tran2")
    window.quick_input.accept(message(variant))
    settle(window, tasks=False)
    assert window.controller is other and window.input.toPlainText() == "TB 草稿"
    return_source = target(desktop.context, "ask_amp", view="layout")
    window.quick_input.accept(message(return_source))
    settle(window, tasks=False)
    assert window.controller is original and window.input.toPlainText() == "Amp 草稿"
    assert window.drafts.load(other)[0] == "TB 草稿"
    assert len(window.sessions.controllers) == 2
    assert desktop.notices == [("accepted", {"id": key})
                               for key in ("ask_tb", "ask_tb2", "ask_amp")]


def test_slow_binding_lookup_does_not_stall_qt_or_override_later_activation(
    window, desktop, monkeypatch,  # noqa: F811
):
    entered, release = threading.Event(), threading.Event()
    owner = threading.get_ident()
    original = desktop.broker.binding_snapshot
    selected = window.controller

    def snapshot(*args, **kwargs):
        assert threading.get_ident() != owner
        if threading.current_thread().name == "copilot-commands":
            entered.set()
            assert release.wait(5)
        return original(*args, **kwargs)

    ticks = []
    timer = QTimer(window)
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start(5)
    monkeypatch.setattr(desktop.broker, "binding_snapshot", snapshot)
    try:
        window.quick_input.accept(message(target(desktop.context, "ask_other", cell="Other")))
        wait_until(entered.is_set)
        QTest.qWait(60)
        assert len(ticks) >= 2 and desktop.notices == []
        # A newer view activation makes the pending routing receipt passive.
        window.page.activate_session(selected.session_id)
        window.input.setPlainText("保留当前视图的新草稿")
    finally:
        release.set()
        timer.stop()
    settle(window, tasks=False)
    assert window.controller is selected
    assert window.input.toPlainText() == "保留当前视图的新草稿"
    assert desktop.notices == [("accepted", {"id": "ask_other"})]
    destination = window.sessions.quick_input.destinations["ask_other"]
    assert destination is not selected and len(destination.inbox.pending) == 1


def test_accepted_receipt_precedes_page_open_failure(window, desktop, monkeypatch):  # noqa: F811
    def failed(_session_id):
        raise ValueError("page could not open")

    monkeypatch.setattr(window.page, "activate_session", failed)
    window.quick_input.accept(message(target(desktop.context, "ask_new", cell="Other")))
    settle(window, tasks=False)
    assert desktop.notices == [("accepted", {"id": "ask_new"})]
    destination = window.sessions.quick_input.destinations["ask_new"]
    assert len(destination.inbox.pending) == 1
    assert (destination.inbox.directory / "ask_new.json").exists()


def test_refused_quick_input_displays_one_error_and_preserves_draft(window, desktop, monkeypatch):  # noqa: F811
    calls = []
    def refuse(_controller, request):
        calls.append(request)
        raise ValueError("原请求未接收")
    monkeypatch.setattr(window.api, "accept_quick", refuse)
    window.input.setPlainText("保留草稿")
    request = message(target(desktop.context, "ask_refused"))
    window.quick_input.accept(request)
    assert notice_text(window) == "错误 · 快速输入未接收\n原请求未接收"
    assert window.input.toPlainText() == "保留草稿"
    assert desktop.notices == [("rejected", {"id": "ask_refused"})]
    dismiss_notice(window)
    QApplication.processEvents()
    assert not notice_text(window) and calls == [request]


def test_lost_host_ack_warns_without_repeating_accepted_input(window, desktop, monkeypatch):  # noqa: F811
    monkeypatch.setattr(window.quick_input, "notice", lambda *_args, **_kwargs: False)
    window.quick_input.accept(message(target(desktop.context, "ask_unconfirmed")))
    settle(window, tasks=False)
    assert "警告 · 快速输入回执未送达" in notice_text(window)
    assert "勿重复提交" in notice_text(window)
    destination = window.sessions.quick_input.destinations["ask_unconfirmed"]
    assert len(destination.inbox.pending) == 1
    dismiss_notice(window)
    QApplication.processEvents()
    assert len(destination.inbox.pending) == 1
