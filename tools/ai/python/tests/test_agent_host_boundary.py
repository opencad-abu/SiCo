"""Initial host registration and notice delivery never wait inside Qt callbacks."""

import json
import os
import queue
import threading
from concurrent.futures import Future
from types import SimpleNamespace

import pytest
from agent_gui_wait import settle, wait_until
from info_app_fixtures import notice_text

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from sico.core.contracts import BoundContext
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.providers.mock import ScriptedProvider
from sico.service.notices import HostNotices
from sico.storage.journal import SessionJournal
from sico_ui.lifecycle import DesktopControl
from agent_quick_fixture import QuickInput
from agent_window_fixture import create_window as AssistantWindow


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "initial") as journal:
        loop = PythonBackend(ScriptedProvider([]), ToolRegistry(), journal,
                         BoundContext("instance", "generation", "initial", {}))
        loop._event("session.created", {})
        widget = AssistantWindow(loop)
        try:
            settle(widget)
            yield widget
        finally:
            widget.sessions.close()
            assert widget.sessions.wait(5)
            widget.lifecycle.persistent = False
            widget.close()
            widget.deleteLater()
            app.processEvents()


def heartbeat(window):
    ticks = []
    timer = QTimer(window)
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start(5)
    try:
        QTest.qWait(60)
        assert len(ticks) >= 2
    finally:
        timer.stop()


def test_host_notice_sits_above_the_input_and_leaves_the_status_bar(window):
    """宿主通知贴在输入框上方；状态栏右侧只保留“基座/模型”标签。"""

    window.show()
    window.actions.update_host_notices("stalled")
    settle(window, tasks=False)
    notice = window.host_notice_status
    assert not notice.isHidden()
    assert not window.statusBar().isAncestorOf(notice)
    assert window.centralWidget().isAncestorOf(notice)
    notice_top = notice.mapTo(window, notice.rect().topLeft()).y()
    input_top = window.input.mapTo(window, window.input.rect().topLeft()).y()
    assert notice_top < input_top
    assert "暂时延迟" in notice.text()


def quick_message(window):
    current = window.controller.current
    context = BoundContext(current.instance_id, current.generation, "quick", current.snapshot)
    return {"kind": "submit", "id": "quick", "text": "Read the captured source",
            "context": context.record()}


def test_slow_registration_precedes_manual_and_quick_input_without_stalling_qt(window, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    controller, owner = window.controller, threading.get_ident()
    order, notices = [], []
    controller.paused = True
    save = controller.inbox._save

    class Broker:
        def register_session(self, session_id, context):
            assert threading.get_ident() != owner
            assert session_id == controller.session_id
            if not order:
                entered.set()
                assert release.wait(5)
            order.append("register")

        def register_target(self, context):
            assert threading.get_ident() != owner

    def save_input(record):
        assert threading.get_ident() != owner
        order.append(record["message"]["text"])
        return save(record)

    def notice(kind, **data):
        if kind == "accepted":
            assert (controller.inbox.directory / (data["id"] + ".json")).exists()
        notices.append(kind)

    monkeypatch.setattr(controller.inbox, "_save", save_input)
    window.quick_input = QuickInput(window, Broker(), notice)
    try:
        assert entered.wait(2)
        window.input.setPlainText("Manual input")
        window.actions.submit()
        window.quick_input.accept(quick_message(window))
        heartbeat(window)
        assert not order and not notices
        assert not list(controller.inbox.directory.glob("*.json"))
        release.set()
        settle(window, tasks=False)
        assert order[:4] == ["register", "Manual input", "register", "Read the captured source"]
        assert notices == ["accepted"]
        assert len(controller.inbox.pending) == 2
    finally:
        release.set()


def test_registration_failure_prevents_queued_inputs_and_startup_work(window, monkeypatch, caplog):
    entered, release = threading.Event(), threading.Event()
    controller, notices = window.controller, []
    monkeypatch.setattr(controller.loop.tools, "schemas", lambda: [{"name": "get_context"}])

    class Broker:
        def register_session(self, session_id, context):
            entered.set()
            assert release.wait(5)
            raise ValueError("Registration refused")

        def register_target(self, context):
            raise AssertionError("Failed registration must prevent accepting inputs")

    window.quick_input = QuickInput(window, Broker(), lambda kind, **data: notices.append(kind))
    try:
        assert entered.wait(2)
        window.actions.initialize_context()
        window.input.setPlainText("Retain my draft")
        window.actions.submit()
        window.quick_input.accept(quick_message(window))
        heartbeat(window)
        release.set()
        settle(window)
        assert controller.fault
        assert not window.send.isEnabled()
        assert notices == ["rejected"]
        assert window.input.toPlainText() == "Retain my draft"
        assert not list(controller.inbox.directory.glob("*.json"))
        assert not controller.loop.provider.requests
        assert not any(event["kind"] == "task.started" for event in controller.journal.events())
        diagnostics = [record for record in caplog.records if record.name.endswith(".worker")]
        assert len(diagnostics) == 2
        assert all(record.thread != threading.get_ident() for record in diagnostics)
        assert "Quick input target attach failed" in diagnostics[0].getMessage()
        assert "Quick input was not accepted" in diagnostics[1].getMessage()
    finally:
        release.set()


def test_close_during_registration_keeps_qt_responsive_and_never_starts_inputs(window):
    entered, release = threading.Event(), threading.Event()
    controller, owner, released = window.controller, threading.get_ident(), []

    class Broker:
        def register_session(self, session_id, context):
            assert threading.get_ident() != owner
            entered.set()
            assert release.wait(5)

        def release_session(self, session_id):
            assert threading.get_ident() != owner
            released.append(session_id)

    window.quick_input = QuickInput(window, Broker(), lambda kind, **data: None)
    try:
        assert entered.wait(2)
        window.input.setPlainText("Do not start after close")
        window.actions.submit()
        window.sessions.close()
        heartbeat(window)
        assert controller._shutdown.is_set()
        release.set()
        assert window.sessions.wait(5)
        assert released == [controller.session_id]
        assert not controller.inbox.pending
        assert not controller.loop.provider.requests
    finally:
        release.set()


def test_late_registration_failure_does_not_overwrite_another_activation(window):
    entered, release = threading.Event(), threading.Event()

    class Broker:
        def register_session(self, session_id, context):
            entered.set()
            assert release.wait(5)
            raise ValueError("Old registration failed")

    window.quick_input = QuickInput(window, Broker(), lambda kind, **data: None)
    try:
        assert entered.wait(2)
        window.binding.timer.stop()
        window.page.activation += 1
        window.status.setText("Current page status")
        release.set()
        wait_until(lambda: not window.commands.pending)
        assert window.status.text() == "Current page status"
        assert window.commands.error == ""
    finally:
        release.set()


def test_rejected_registration_command_prevents_unregistered_tasks(window, monkeypatch):
    def full_queue(*args, **kwargs):
        raise ValueError("Desktop command queue is full")

    monkeypatch.setattr(window.sessions.worker, "_request", full_queue)
    window.quick_input = QuickInput(window, object(), lambda kind, **data: None)
    assert window.controller._shutdown.is_set()
    with pytest.raises(ValueError):
        window.controller.submit("Must not run without registration")


def test_show_and_hide_notices_keep_qt_responsive_under_real_pipe_backpressure(window):
    reader, writer = os.pipe()
    os.set_blocking(writer, False)
    try:
        while True:
            os.write(writer, b"x" * 4096)
    except BlockingIOError:
        pass
    notices = HostNotices(writer)
    try:
        window.lifecycle.notice = notices
        window.lifecycle.persistent = True
        window.restore()
        window.close()
        heartbeat(window)
        assert not notices.failed.is_set()
        os.set_blocking(reader, False)
        received = bytearray()
        try:
            while True:
                received.extend(os.read(reader, 65536))
        except BlockingIOError:
            pass
        notices.close()
        assert notices.wait(2)
        try:
            while True:
                received.extend(os.read(reader, 65536))
        except BlockingIOError:
            pass
        messages = [json.loads(line) for line in bytes(received).lstrip(b"x").splitlines()]
        assert messages == [{"kind": "shown", "session_id": "initial"}, {"kind": "hidden"}]
        assert not notices.failed.is_set(), notices.error
    finally:
        notices.close()
        assert notices.wait(2)
        os.close(reader)
        os.close(writer)


def test_notice_channel_failure_keeps_the_session_until_the_host_pipe_closes():
    """A busy host stalls notice delivery; that must not end a live session."""

    reader = SimpleNamespace(closed=threading.Event(), lines=queue.Queue(), close=lambda **kw: None)
    notices = SimpleNamespace(failed=threading.Event())
    calls = []
    control = DesktopControl(reader, notices=notices)
    control.window = SimpleNamespace(
        actions=SimpleNamespace(update_host_notices=lambda state: None),
        quick_input=SimpleNamespace(close=lambda: calls.append("close")),
        request_quit=lambda: calls.append("quit"),
    )
    try:
        notices.failed.set()
        control.poll()
        assert not control.closing
        assert calls == []
        control.poll()
        assert not control.closing and calls == []
        # The owning host really left: the control pipe closes and the desktop ends.
        reader.closed.set()
        control.poll()
        assert control.closing
        assert calls == ["quit"]
    finally:
        control.close()


def test_attach_control_reuses_the_existing_window_and_switches_session():
    reader = SimpleNamespace(closed=threading.Event(), lines=queue.Queue(), close=lambda **kw: None)
    control = DesktopControl(reader)
    calls = []
    page = SimpleNamespace(session=SimpleNamespace(session_id="old"), opening=False)

    def activate(session_id):
        calls.append(("session", session_id))
        page.session = SimpleNamespace(session_id=session_id)

    page.activate_session = activate
    control.window = SimpleNamespace(
        restore=lambda: calls.append("restore"),
        focus_input=lambda: calls.append("focus"),
        page=page,
        api=SimpleNamespace(control=SimpleNamespace(
            ensure=lambda session: calls.append(("ensure", session.session_id)) or Future())),
        commands=SimpleNamespace(watch=lambda future, **kwargs: calls.append("watch")),
        session_notices=SimpleNamespace(set=lambda *args, **kwargs: None),
    )
    session_id = "b" * 32
    try:
        reader.lines.put({"kind": "attach", "session_id": session_id})
        control.poll()
        assert calls == ["restore", ("session", session_id), ("ensure", session_id),
                         "watch", "focus"]
        assert not control.closing
        # An attach that arrives before the window is attached is not dropped.
        control.window = None
        reader.lines.put({"kind": "attach", "session_id": "c" * 32})
        control.poll()
        late_page = SimpleNamespace(session=SimpleNamespace(session_id="old"), opening=False)

        def late_activate(session_id):
            calls.append(("late", session_id))
            late_page.session = SimpleNamespace(session_id=session_id)

        late_page.activate_session = late_activate
        window = SimpleNamespace(
            restore=lambda: None,
            focus_input=lambda: None,
            page=late_page,
            api=SimpleNamespace(control=SimpleNamespace(
                ensure=lambda session: calls.append(("late-ensure", session.session_id)) or Future())),
            commands=SimpleNamespace(watch=lambda future, **kwargs: calls.append("late-watch")),
            session_notices=SimpleNamespace(set=lambda *args, **kwargs: None),
            quick_input=SimpleNamespace(accept=lambda message: None),
        )
        control.attach(window)
        assert calls[-3:] == [("late", "c" * 32), ("late-ensure", "c" * 32), "late-watch"]
    finally:
        control.close()


def test_delayed_host_notice_status_recovers_without_closing_session(window):
    reader = SimpleNamespace(closed=threading.Event(), lines=queue.Queue(), close=lambda **kw: None)
    notices = SimpleNamespace(failed=threading.Event(), stalled=threading.Event())
    control = DesktopControl(reader, notices=notices)
    try:
        control.attach(window)
        notices.stalled.set()
        control.poll()
        assert "暂时延迟" in window.host_notice_status.text()
        assert not window.controller.closing
        notices.stalled.clear()
        control.poll()
        assert window.host_notice_status.isHidden()
        notices.failed.set()
        control.poll()
        assert "通知不可用" in window.host_notice_status.text()
        assert not window.controller.closing
    finally:
        control.close()


def test_failed_quick_ack_preserves_accepted_input_once(window):
    controller = window.controller
    controller.paused = True
    broker = SimpleNamespace(register_target=lambda context: None,
                             register_session=lambda *args: None)
    quick = QuickInput(window, broker, lambda kind, **data: False)
    settle(window, tasks=False)
    message = quick_message(window)
    quick.accept(message)
    settle(window, tasks=False)
    assert len(controller.inbox.pending) == 1
    assert controller.inbox.pending[0]["id"] == message["id"]
    assert "快速输入已接收" in window.host_notice_status.text()
    assert "勿重复提交" in window.host_notice_status.text()
    assert not controller.closing


def test_failed_reconnect_does_not_claim_it_was_sent(window, monkeypatch):
    monkeypatch.setattr("sico_ui.window_actions.confirm",
                        lambda *args, **kwargs: True)
    window.lifecycle.notice = lambda *args, **kwargs: False
    window.actions.reconnect_instance()
    assert "重连请求未发送" in notice_text(window)
    assert "重连请求未发送" not in window.status.text()
    assert not window.controller.closing
