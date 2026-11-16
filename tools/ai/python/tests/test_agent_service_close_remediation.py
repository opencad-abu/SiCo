"""Closing observes original end receipts through drain, faults, and page changes."""

import threading
from concurrent.futures import Future
from contextlib import ExitStack
from types import SimpleNamespace

import pytest
from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service, open_session
from agent_service_gui_fixtures import remote_window
from agent_worker_fixtures import heartbeat
from PyQt5.QtCore import QPoint, Qt, QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QPushButton
from sico.service.frontend_hub import FrontendHub
from sico.service.service_lifecycle import PendingWork
from sico.service.session_end import SessionEnd
from sico.transport.framing import ProtocolError
from sico_ui.close_choice import PLATFORM_CLOSE_NOTE, ROW, STOP_SERVICE, WINDOW_ONLY
from sico_ui.force_service_stop import FORCE_STOP
from sico_ui.service_connection import attach_service_connection
from sico_ui.si_prompt import SiConfirm
from test_agent_service_control import attach
from test_agent_service_control_gui import window_for


@pytest.mark.parametrize("entry_point", ["close_dialog", "service_menu"])
def test_close_waits_for_slow_drain_then_stops_without_reconnecting(
        tmp_path, monkeypatch, entry_point):
    entered, release = threading.Event(), threading.Event()
    original = SessionEnd._drain
    def delayed(ending, *args):
        entered.set()
        assert release.wait(10)
        return original(ending, *args)
    monkeypatch.setattr(SessionEnd, '_drain', delayed)
    with remote_window(tmp_path, monkeypatch) as (service, window):
        attach_service_connection(window)
        connection = window.service_connection
        calls = []
        stop = window.api.stop_service
        monkeypatch.setattr(window.api, 'stop_service', lambda: (calls.append('stop'), stop())[1])
        try:
            window.show()
            if entry_point == "service_menu":
                connection.stop_action.trigger()
                connection.stop_action.trigger()  # Duplicate activation must not repeat cleanup.
                assert window.close_choice.dialog is None
            else:
                entry = next(action.defaultWidget() for action in window.session_menu.actions()
                             if action.text() == '关闭SiCo')
                QTest.mouseClick(entry, Qt.LeftButton)
                box = window.close_choice.dialog
                assert box is not None
                box.button(STOP_SERVICE).click()
            wait_until(entered.is_set)
            wait_until(lambda: connection.ending.operations[window.page.session].state == 'closing')
            heartbeat(window)
            assert window.close_choice.busy and not calls
            assert '自动重连' not in connection.label.text()
            release.set()
            wait_until(lambda: window.lifecycle.closing, timeout=8)
            assert calls == ['stop'] and not service.owner.controllers
            assert connection.ending.operations[window.page.session].state == 'ended'
        finally:
            release.set()


def test_lost_end_ack_observes_same_intent_without_repeating_it(tmp_path, monkeypatch):
    original = FrontendHub.command
    submitted = []
    def lose_ack(hub, wire, operation, params):
        result = original(hub, wire, operation, params)
        if operation == 'end_session':
            submitted.append(params['operation_id'])
            raise ProtocolError('lost original end receipt')
        return result
    with remote_window(tmp_path, monkeypatch) as (service, window):
        attach_service_connection(window)
        monkeypatch.setattr(FrontendHub, 'command', lose_ack)
        window.close_choice.stop_everything()
        wait_until(lambda: window.lifecycle.closing, timeout=8)
        assert len(submitted) == 1 and not service.owner.controllers


def test_two_row_ends_keep_independent_receipts(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        open_session(service, 'other')
        api, token = attach(stack, service, tmp_path)
        other = api.open_session('other').result(5)
        window = window_for(stack, api, token)
        results = []
        panel = window.service_connection.ending
        panel.end(token, lambda row: results.append(row.token), lambda error: results.append(error))
        panel.end(other, lambda row: results.append(row.token), lambda error: results.append(error))
        wait_until(lambda: len(results) == 2, timeout=8)
        assert set(results) == {token, other}
        assert not service.owner.controllers


def test_close_stops_the_whole_project_service(tmp_path, monkeypatch):
    with remote_window(tmp_path, monkeypatch) as (service, window):
        open_session(service, 'left-over')
        attach_service_connection(window)
        wait_until(lambda: window.api.session('left-over') is not None)
        assert set(window.page.opened) == {'live'}
        window.service_connection.stop_action.trigger()
        wait_until(lambda: window.lifecycle.closing)
        assert '自动重连' not in window.service_connection.label.text()


def test_blocked_stop_exposes_force_when_only_background_jobs_remain(tmp_path, monkeypatch):
    with remote_window(tmp_path, monkeypatch) as (_service, window):
        attach_service_connection(window)
        future = Future()
        future.set_result(())
        monkeypatch.setattr(window.api.catalog, "refresh", lambda: future)
        blocked = window.close_choice.blocked
        try:
            window.close_choice.stopped(SimpleNamespace(
                status=SimpleNamespace(outcome="blocked", pending=PendingWork(jobs=1))))
            wait_until(lambda: blocked.dialog is not None
                       and blocked.dialog.button("force") is not None)
            assert not blocked.dialog.button("finish").isEnabled()
            assert blocked.dialog.button("force").isEnabled()
            wait_until(lambda: "后台作业不能通过结束会话清理"
                       in blocked.dialog.message.text())
            assert "后台作业不能通过结束会话清理" in blocked.dialog.message.text()
            assert "bkill" in blocked.dialog.message.text()
            blocked.dialog.button("force").click()
            wait_until(lambda: window.close_choice.force.dialog is not None)
            window.close_choice.force.dialog.reject()
        finally:
            blocked.close()


def test_dialog_opens_while_status_pending_and_late_reply_is_discarded(tmp_path, monkeypatch):
    with remote_window(tmp_path, monkeypatch) as (_service, window):
        attach_service_connection(window)
        delayed = Future()
        monkeypatch.setattr(window.api, 'service_status', lambda: delayed)
        pulse = []
        QTimer.singleShot(0, lambda: pulse.append(True))
        window.close_choice.ask()
        box = window.close_choice.dialog
        assert box is not None and not delayed.done()
        wait_until(lambda: pulse)
        box.reject()
        assert window.close_choice.dialog is None and delayed.cancelled()
        assert not window.lifecycle.closing


def test_system_close_asks_once_and_default_suspends_the_service(tmp_path, monkeypatch):
    with remote_window(tmp_path, monkeypatch) as (service, window):
        attach_service_connection(window)
        window.show()
        window.close()
        box = window.close_choice.dialog
        assert box is not None and not window.lifecycle.closing
        window.close()
        assert window.close_choice.dialog is box
        assert box.button(WINDOW_ONLY).isDefault()
        box.button(WINDOW_ONLY).click()
        wait_until(lambda: window.lifecycle.closing)
        assert "live" in service.owner.controllers
        assert not service.owner.controllers["live"].closing


def test_close_dialog_shows_the_agreed_row_left_to_right(tmp_path, monkeypatch):
    """关闭对话框固定为 [结束服务] [挂起] [取消]；挂起保留服务。"""
    with remote_window(tmp_path, monkeypatch) as (_service, window):
        attach_service_connection(window)
        window.show()
        window.close_choice.ask()
        box = window.close_choice.dialog
        assert box is not None
        box.adjustSize()
        QApplication.instance().processEvents()
        shown = sorted(box.findChildren(QPushButton),
                       key=lambda button: button.mapTo(box, QPoint(0, 0)).x())
        assert [button.text() for button in shown] == ["结束服务", "挂起", "取消"]
        assert PLATFORM_CLOSE_NOTE in box.message.text()
        # 强制停止只在「服务」菜单里：关闭对话框不再放它，避免误触。
        assert FORCE_STOP not in [button.text() for button in shown]
        assert [action.text() for action in window.service_menu.actions()] == ["停止", FORCE_STOP]


@pytest.mark.parametrize("layout", ["gnome", "mac", "windows", "kde"])
def test_choices_keep_the_agreed_row_order_on_every_desktop_layout(monkeypatch, layout):
    """自绘对话框按注册顺序排版：三个选择在任何桌面约定下都排成同一排。"""
    from PyQt5.QtWidgets import QStyle

    layouts = {"gnome": QStyle.SH_DialogButtonLayout, "mac": QStyle.SH_DialogButtonLayout}
    monkeypatch.setattr(QStyle, "styleHint", lambda *_args, **_kwargs: layouts.get(layout))
    dialog = SiConfirm("关闭 SiCo", "确认顺序")
    for key, text in ROW:
        dialog.add_choice(key, text)
    shown = sorted(dialog.findChildren(QPushButton),
                   key=lambda button: button.mapTo(dialog, QPoint(0, 0)).x())
    assert [button.text() for button in shown] == ["结束服务", "挂起", "取消"]
    dialog.deleteLater()


def test_failed_cleanup_still_stops_service_and_preserves_original_operation(tmp_path, monkeypatch):
    from agent_service_business_fixtures import Provider
    from sico.core.contracts import NeedsReconcile
    class Unknown(Provider):
        def stream(self, *args):
            raise NeedsReconcile('unconfirmed external tool')
            yield
    with remote_window(tmp_path, monkeypatch, provider=Unknown()) as (service, window):
        attach_service_connection(window)
        token = window.page.session
        window.api.command(token, 'submit', 'unknown task').result(5)
        assert service.owner.controllers['live'].wait(5)
        loop = service.owner.controllers["live"].loop
        original_close = getattr(loop, "close", lambda: None)
        def cannot_close():
            raise RuntimeError("resource cleanup unavailable")
        monkeypatch.setattr(loop, "close", cannot_close, raising=False)
        try:
            window.close_choice.stop_everything()
            wait_until(lambda: window.lifecycle.closing)
            operation = window.service_connection.ending.operations[token]
            assert operation.state == 'needs_reconcile'
            original = operation.operation_id
            assert operation.operation_id == original
        finally:
            monkeypatch.setattr(loop, "close", original_close)



def test_end_status_refusal_never_replaces_uncertain_original_intent(tmp_path, monkeypatch):
    original = FrontendHub.command
    calls = []
    def uncertain(hub, wire, operation, params):
        if operation == 'end_status':
            raise ValueError('observation temporarily unavailable')
        result = original(hub, wire, operation, params)
        if operation == 'end_session':
            calls.append(params['operation_id'])
            raise ProtocolError('lost end receipt')
        return result
    with remote_window(tmp_path, monkeypatch) as (_service, window):
        attach_service_connection(window)
        monkeypatch.setattr(FrontendHub, 'command', uncertain)
        token = window.page.session
        panel = window.service_connection.ending
        done = []
        panel.end(token, lambda row: done.append(row.state), lambda error: done.append(error))
        wait_until(lambda: done)
        operation = panel.operations[token]
        assert operation.state == 'unknown' and len(calls) == 1
        monkeypatch.setattr(FrontendHub, 'command', original)
        panel.end(token, lambda row: done.append(row.state), lambda error: done.append(error))
        wait_until(lambda: len(done) == 2)
        assert done[-1] == 'ended' and operation.operation_id == calls[0]


def test_unreachable_service_refusal_names_the_next_step(tmp_path, monkeypatch):
    """请求未发出时说明必须指向可执行下一步，不能套用会话归档的收尾文案。"""
    from info_app_fixtures import dismiss_notice, notice_text
    from sico.service.service_errors import RequestUnsent

    with remote_window(tmp_path, monkeypatch) as (_service, window):
        attach_service_connection(window)
        window.close_choice.refused(RequestUnsent("Service connection failed before request"))
        text = notice_text(window)
        assert "项目服务当前不可达" in text and "请求没有发出" in text
        assert "已归档记录无需再次归档" not in text
        assert "结束服务" in text and FORCE_STOP in text
        dismiss_notice(window)
