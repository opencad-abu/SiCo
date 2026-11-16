"""Qt stays responsive while recovery evidence or durable publication waits."""

import threading
from contextlib import ExitStack

from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service, open_session
from agent_service_recovery_fixtures import client, seed
from agent_worker_fixtures import heartbeat
from PyQt5.QtWidgets import QLineEdit
from sico.service.session_recovery import SessionRecovery
from sico.storage.journal import SessionJournal


def window_for(stack, api, token):
    from agent_service_gui_fixtures import ServiceTestWindow as AssistantWindow
    from sico.service.frontend_session import FrontendSession
    from sico_ui.service_connection import ServiceConnection

    window = AssistantWindow(FrontendSession(api, token))
    window.service_connection = ServiceConnection(window)
    stack.callback(window.deleteLater)
    stack.callback(window.request_quit)
    wait_until(lambda: not window.page.opening and window.binding.cursor is not None)
    return window


def test_history_inspection_is_async_and_late_result_does_not_open_dialog(tmp_path, monkeypatch):
    context, _ = seed(tmp_path, monkeypatch)
    with SessionJournal(tmp_path, "old") as journal:
        journal.append_session("session.created", dict(context=context.record()), context)
    entered, release = threading.Event(), threading.Event()
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service, "other")
        api = client(stack, service, tmp_path)
        frontend = api.recovery.open_history("old").result(5)
        window = window_for(stack, api, frontend.session)
        panel = window.service_connection.recovery
        original = SessionRecovery.inspect
        def paused(*args):
            entered.set()
            assert release.wait(8)
            return original(*args)
        monkeypatch.setattr(SessionRecovery, "inspect", paused)
        panel.inspect()
        try:
            wait_until(entered.is_set)
            heartbeat(window)
            assert not release.is_set()
            window.page.activate_session("other")
            wait_until(lambda: not window.page.opening)
            window.input.setPlainText("other draft")
            release.set()
            wait_until(lambda: panel.receipt.pending is None)
            assert panel.dialog is None and window.page.session.session_id == "other"
            assert window.input.toPlainText() == "other draft"
        finally:
            release.set()


def test_history_restore_publishes_paused_runtime_without_switching_or_control(
        tmp_path, monkeypatch):
    seed(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        frontend = api.recovery.open_history("old").result(5)
        window = window_for(stack, api, frontend.session)
        panel = window.service_connection.recovery
        panel.inspect()
        wait_until(lambda: panel.dialog is not None)
        view = api.recovery.inspect("old").result(5)
        original = SessionJournal.append_session
        def paused(journal, kind, *args):
            if kind == "session.recovered":
                entered.set()
                assert release.wait(8)
            return original(journal, kind, *args)
        monkeypatch.setattr(SessionJournal, "append_session", paused)
        field = panel.dialog.findChild(QLineEdit)
        panel.restore(view, field)
        try:
            wait_until(entered.is_set)
            heartbeat(window)
            assert not release.is_set() and "old" not in service.owner.controllers
            assert window.page.session.runtime_id is None
            release.set()
            wait_until(lambda: panel.dialog is not None)
            assert service.owner.controllers["old"].paused and not service.provider.seen
            assert window.page.session.runtime_id is None
            panel.attach("old")
            wait_until(lambda: not window.page.opening
                       and window.page.session.runtime_id is not None)
            assert not api.can_control(window.page.session)
        finally:
            release.set()


def test_session_row_continue_recovers_a_captured_record(tmp_path, monkeypatch):
    """服务窗口右键“继续”直接激活原记录，无恢复对话框。"""

    from sico.service.remote_frontend import RemoteFrontend

    context, _ = seed(tmp_path, monkeypatch)
    with SessionJournal(tmp_path, "old") as journal:
        journal.append_session("session.created", dict(context=context.record()), context)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service, "live")
        api = RemoteFrontend(service.descriptor, tmp_path, "live")
        stack.callback(api.close_desktop)
        frontend = api.open_initial("attach", dict(session_id="live")).result(5)
        api.control.request(frontend.session, "acquire").result(5)
        window = window_for(stack, api, frontend.session)
        assert api.session("old") is None

        window.navigation._continue_session("old")
        panel = window.service_connection.recovery
        wait_until(lambda: not window.page.opening and window.page.session.session_id == "old"
                   and window.input.isEnabled())
        assert window.page.reviewing is None and panel.dialog is None
        assert api.can_control(window.page.session)
        assert not service.owner.controllers["old"].paused
        assert not service.provider.seen
        heartbeat(window)


def test_broken_event_stream_recovers_in_place(tmp_path, monkeypatch):
    """事件流中断：服务窗口就地重连恢复，不弹“已暂停当前任务”的硬错误。"""

    from agent_service_gui_fixtures import remote_window
    from sico_ui.service_connection import attach_service_connection

    with remote_window(tmp_path, monkeypatch) as (_service, window):
        attach_service_connection(window)
        errors = []
        monkeypatch.setattr(window.info_app, "error",
                            lambda *args, **kwargs: errors.append(args))
        broken = window.binding.delivery

        def fail(*_args, **_kwargs):
            raise ValueError("stream broke")

        broken.apply = fail
        window.binding.poll()
        wait_until(lambda: window.service_connection._retry.attempts == 1)
        wait_until(lambda: (window.binding.delivery is not None
                            and window.binding.delivery is not broken
                            and not window.binding.failed
                            and not window.page.opening))
        assert window.service_connection.attached()
        assert not errors                               # 自动恢复不弹硬错误
        assert window.binding.cursor is not None
