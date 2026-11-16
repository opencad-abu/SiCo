"""Qt derives control ownership itself; drafts and page identity survive every handover."""

import threading
from contextlib import ExitStack

import pytest
from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service, open_session
from agent_service_gui_fixtures import ServiceTestWindow as AssistantWindow
from agent_service_gui_fixtures import remote_window
from agent_service_gui_fixtures import startup_args
from agent_worker_fixtures import heartbeat
from test_agent_service_control import attach

from sico.service.frontend_startup import FrontendStartup
from sico.service.session_control import SessionControl
from sico.transport.framing import ProtocolError
from sico_ui.service_connection import ServiceConnection


def window_for(stack, api, token):
    from sico.service.frontend_session import FrontendSession

    window = AssistantWindow(FrontendSession(api, token))
    window.service_connection = ServiceConnection(window)
    stack.callback(window.deleteLater)
    stack.callback(window.request_quit)
    wait_until(lambda: not window.page.opening and window.presentation.state is not None)
    return window


def test_two_windows_show_read_only_until_the_holder_disconnects(tmp_path, monkeypatch):
    """窗口自己取得控制权：第二个窗口只读，等第一个窗口断开后自动接管。"""
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        a, token = attach(stack, service, tmp_path)
        b, _ = attach(stack, service, tmp_path)
        first, second = window_for(stack, a, token), window_for(stack, b, token)
        second.input.setPlainText("observer draft remains")
        # 没人持有时第一个窗口自动取得写权限，第二个窗口保持只读并说明原因。
        wait_until(lambda: first.send.isEnabled() and a.can_control(token))
        wait_until(lambda: "其他窗口" in second.service_connection.control.label.text())
        assert not second.send.isEnabled()
        a.close_desktop()  # 旧窗口断开：第二个窗口自动接管，草稿与页面都不丢
        wait_until(lambda: b.can_control(token))
        wait_until(lambda: second.send.isEnabled())
        assert second.input.toPlainText() == "observer draft remains"
        second.actions.submit()
        wait_until(lambda: not second.input.toPlainText())
        assert service.owner.controllers["live"].wait(5)
        assert service.provider.seen == ["observer draft remains"]


def test_reconnect_display_does_not_restore_send_permission(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        window = window_for(stack, api, token)
        wait_until(lambda: window.send.isEnabled())
        with pytest.raises(ProtocolError):
            api._commands.request("invalid", {}).result(5)
        window.service_connection.reconnect()
        wait_until(lambda: not window.page.opening and not window.binding.failed)
        wait_until(lambda: not window.send.isEnabled())
        # 重连把旧连接换掉后，窗口自己恢复控制权（recoverable → resume），不用点菜单。
        wait_until(lambda: window.send.isEnabled())


def test_late_control_receipt_does_not_activate_page_or_end_task(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = SessionControl.execute
    def paused(control, connection, params):
        value = original(control, connection, params)
        if params["action"] == "acquire" and params["address"]["session_id"] == "live":
            entered.set()
            assert release.wait(5)
        return value
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        open_session(service, "other")
        api, token = attach(stack, service, tmp_path)
        other = api.open_session("other").result(5)
        window = window_for(stack, api, token)
        panel = window.service_connection.control
        wait_until(lambda: not api.can_control(token))
        monkeypatch.setattr(SessionControl, "execute", paused)
        panel.request("acquire")
        try:
            wait_until(entered.is_set)
            heartbeat(window)
            window.page.activate_session("other")
            heartbeat(window)
            assert not release.is_set()
            release.set()
            wait_until(lambda: not window.page.opening and panel.receipt.pending is None)
            # 迟到的回执不改变页面：新页面照样由窗口自动取得写权限，旧会话的任务不被结束。
            assert window.page.session.session_id == "other"
            wait_until(lambda: api.can_control(other))
            assert not service.owner.controllers["live"].closing
        finally:
            release.set()


def test_reopening_existing_session_never_implicitly_reacquires_control(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch), ExitStack() as stack:
        args = startup_args(tmp_path, "demo")
        first = FrontendStartup(args, lambda *_a, **_k: None, environment={})
        stack.callback(first.close)
        ready = first.ready.result(5)
        assert ready.initialize and ready.frontend.api.can_control(ready.frontend.session)
        ready.frontend.api.control.request(ready.frontend.session, "release").result(5)
        second = FrontendStartup(args, lambda *_a, **_k: None, environment={})
        stack.callback(second.close)
        observer = second.ready.result(5)
        assert observer.frontend.session == ready.frontend.session
        assert not observer.initialize
        assert not observer.frontend.api.can_control(observer.frontend.session)


def test_crashed_window_hands_control_back_automatically(tmp_path, monkeypatch):
    """窗口崩溃（连接断开）后重开：新窗口自动接管，用户不必去点控制权菜单。"""
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        first, token = attach(stack, service, tmp_path)
        first.control.request(token, "acquire").result(5)
        assert first.can_control(token)
        first.close_desktop()  # 旧窗口崩溃：控制连接断开但会话还在服务里
        second, _ = attach(stack, service, tmp_path)
        window = window_for(stack, second, token)
        wait_until(lambda: second.can_control(token))
        # 自动接管后状态栏没有只读提示，输入区随即可写。
        wait_until(lambda: window.service_connection.control.label.text() == ""
                   and window.send.isEnabled())
        # 自动接管同样留下证据：接管 + 上一位控制者已断开。
        recorded = [event["payload"] for event in service.owner.controllers["live"].journal.events()
                    if event["kind"] == "session.control"]
        assert recorded[-1]["action"] == "takeover"
        assert recorded[-1]["previous_disconnected"] is True
        heartbeat(window)


def test_row_menu_ends_the_session_it_names(tmp_path, monkeypatch):
    from PyQt5.QtWidgets import QMenu
    """「结束会话…」在行上：右键当前会话并确认后，会话结束、窗口不退出。"""
    
    import sico_ui.navigation_menu as navigation_menu

    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        api, token = attach(stack, service, tmp_path)
        window = window_for(stack, api, token)
        wait_until(lambda: window.service_connection and api.can_control(token))
        chosen = {}

        class Menu(QMenu):
            def addAction(self, text):
                action = super().addAction(text)
                chosen[str(text)] = action
                return action

            def exec_(self, *_args):
                return chosen.get("结束并归档")

        monkeypatch.setattr(navigation_menu, "QMenu", Menu)
        item = window.navigation.current.topLevelItem(0)
        monkeypatch.setattr(window.navigation.current, "itemAt", lambda position: item)
        window.navigation._menu(window.navigation.current.viewport().rect().center(),
                                window.navigation.current)
        assert "结束并归档" in chosen and chosen["结束并归档"].isEnabled()
        assert "删除" not in chosen  # 活动会话只能结束并归档
        panel = window.service_connection.ending
        assert panel.dialog is not None
        panel.dialog.button("accept").click()
        wait_until(lambda: "live" not in service.owner.controllers)
        assert not window.lifecycle.closing
        # 归档之后同一行变成记录：这时才出现"删除"。
        from PyQt5.QtCore import Qt

        def archived_row():
            for index in range(window.navigation.list.topLevelItemCount()):
                item = window.navigation.list.topLevelItem(index)
                if item.data(0, Qt.UserRole) == "live":
                    return item
            return None

        wait_until(lambda: archived_row() is not None)
        chosen.clear()
        item = archived_row()
        monkeypatch.setattr(window.navigation.list, "itemAt", lambda position: item)
        window.navigation._menu(window.navigation.list.viewport().rect().center(),
                                window.navigation.list)
        assert "删除" in chosen and "结束并归档" not in chosen
        heartbeat(window)


def test_broken_stream_recovers_without_a_manual_reconnect(tmp_path, monkeypatch):
    """断线由窗口自己重连：用户不需要先点「重试连接」。"""
    from sico.transport.framing import ProtocolError

    with remote_window(tmp_path, monkeypatch) as (service, window):
        from sico_ui.service_connection import attach_service_connection

        attach_service_connection(window)
        wait_until(lambda: window.service_connection.attached())
        window.binding.timer.stop()
        with pytest.raises(ProtocolError):
            window.api._events.request("invalid", {}).result(5)
        window.binding.poll()
        assert window.binding.failed
        wait_until(lambda: not window.binding.failed and window.binding.cursor is not None,
                   timeout=20)
        assert window.service_connection.attached()
        assert not service.owner.controllers["live"].closing
        heartbeat(window)
