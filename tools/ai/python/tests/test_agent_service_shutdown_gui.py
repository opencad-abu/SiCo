"""End confirmations and asynchronous receipts stay with their original Qt page."""

import threading
from contextlib import ExitStack

from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service, open_session
from agent_worker_fixtures import heartbeat
from info_app_fixtures import notice_text
from sico.service.session_end import SessionEnd
from test_agent_service_control import attach
from test_agent_service_control_gui import window_for


def test_end_confirmation_is_nonmodal_and_ends_the_named_session(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        open_session(service, "other")
        api, token = attach(stack, service, tmp_path)
        api.open_session("other").result(5)
        api.control.request(token, "acquire").result(5)
        window = window_for(stack, api, token)
        panel = window.service_connection.ending
        panel.confirm("live")
        assert panel.dialog is not None
        heartbeat(window)
        window.page.activate_session("other")
        wait_until(lambda: not window.page.opening)
        panel.dialog.button("accept").click()
        # 确认框点名了会话：切页也结束它，别的会话不受影响。
        wait_until(lambda: "live" not in service.owner.controllers)
        assert not service.owner.controllers["other"].closing


def test_end_receipt_late_after_page_switch_preserves_sibling_draft(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = SessionEnd.request
    def paused(ending, address, *args):
        result = original(ending, address, *args)
        entered.set()
        assert release.wait(8)
        return result
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        open_session(service, "other")
        api, token = attach(stack, service, tmp_path)
        other = api.open_session("other").result(5)
        api.control.request(token, "acquire").result(5)
        window = window_for(stack, api, token)
        panel = window.service_connection.ending
        monkeypatch.setattr(SessionEnd, "request", paused)
        panel.request(token)
        try:
            wait_until(entered.is_set)
            heartbeat(window)
            assert not release.is_set()
            window.page.activate_session("other")
            wait_until(lambda: not window.page.opening)
            window.input.setPlainText("sibling draft")
            release.set()
            wait_until(lambda: not panel.pending)
            assert window.page.session == other
            assert window.input.toPlainText() == "sibling draft"
            assert not service.owner.controllers["other"].closing
        finally:
            release.set()


def test_end_current_page_keeps_window_and_presents_terminal_result(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        window = window_for(stack, api, token)
        window.input.setPlainText("unsent draft")
        panel = window.service_connection.ending
        panel.confirm(token.session_id)
        panel.dialog.button("accept").click()
        wait_until(lambda: panel.label.text() == "会话已结束")
        assert not window.lifecycle.closing
        assert not window.send.isEnabled() and window.input.toPlainText() == "unsent draft"
        assert not service.owner.controllers
        assert "记录已保留" not in panel.label.text()
        panel.observe()
        assert "信息 · 会话结束结果" in notice_text(window)
        assert "记录已保留" in notice_text(window)
