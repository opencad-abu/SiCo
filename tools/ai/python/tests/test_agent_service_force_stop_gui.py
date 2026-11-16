"""Emergency stop is explicit, usable during stalled cleanup, and asynchronous."""

import os
import signal
from concurrent.futures import Future
from contextlib import ExitStack

import pytest
from agent_gui_wait import wait_until
from agent_service_gui_fixtures import remote_window
from agent_service_lifecycle_fixtures import service_process
from agent_worker_fixtures import heartbeat
from info_app_fixtures import dismiss_notice, notice_text
from sico.service.service_force_stop import ForceStopResult
from sico_ui.close_choice import STOP_SERVICE, WINDOW_ONLY
from sico_ui.force_service_stop import FORCE_STOP
from sico_ui.si_prompt import CANCEL
from sico_ui.service_connection import attach_service_connection
from test_agent_service_control_gui import window_for
from test_agent_service_shutdown_process import open_demo


def click(box, key):
    box.button(key).click()


def test_confirmation_names_impact_cancel_is_default_and_sends_nothing(tmp_path, monkeypatch):
    """强制停止只从「服务」菜单进入；确认框默认取消，不误发请求。"""
    from PyQt5.QtWidgets import QPushButton

    with remote_window(tmp_path, monkeypatch) as (_service, window):
        attach_service_connection(window)
        calls = []
        monkeypatch.setattr(window.api, "force_stop_service", lambda: calls.append(True))
        # 关闭对话框不再放强制停止，避免误触。
        window.close_choice.ask()
        closing = window.close_choice.dialog
        assert FORCE_STOP not in [button.text()
                                  for button in closing.findChildren(QPushButton)]
        click(closing, CANCEL)
        window.service_connection.force_action.trigger()
        box = window.close_choice.force.dialog
        text = box.message.text()
        assert "所有会话都会断开" in text and "不会自动重发" in text
        assert "Virtuoso 或 LSF" in text and "其他工程不受影响" in text
        assert box.button(CANCEL).isDefault()
        heartbeat(window)
        click(box, CANCEL)
        assert not calls and not window.lifecycle.closing


def test_force_stop_available_during_hung_normal_close_and_ignores_late_failure(tmp_path, monkeypatch):
    with remote_window(tmp_path, monkeypatch) as (service, window):
        attach_service_connection(window)
        ending, force_calls, quit_calls = [], [], []
        future = Future()
        monkeypatch.setattr(window.service_connection.ending, "end",
            lambda token, done, failed: ending.append((done, failed)))
        monkeypatch.setattr(window.api, "force_stop_service",
            lambda: (force_calls.append(True), future)[1])
        monkeypatch.setattr(window, "request_quit", lambda: quit_calls.append(True))
        window.close_choice.stop_everything()
        assert window.close_choice.busy and ending
        window.service_connection.force_action.trigger()
        click(window.close_choice.force.dialog, FORCE_STOP)
        assert force_calls == [True] and window.close_choice.busy
        heartbeat(window)
        window.service_connection.force_action.trigger()
        assert force_calls == [True]
        window.close_choice.ask()
        closing = window.close_choice.dialog
        assert not closing.button(STOP_SERVICE).isEnabled()
        assert not closing.button(WINDOW_ONLY).isEnabled()
        assert closing.button(CANCEL).isDefault()
        click(closing, CANCEL)
        ending[0][1](ValueError("late normal-close failure"))
        assert not notice_text(window)
        future.set_result(ForceStopResult(service.descriptor.service_id))
        wait_until(lambda: quit_calls)
        assert quit_calls == [True] and not window.close_choice.busy


def test_force_stop_failure_is_chinese_popup_and_can_retry(tmp_path, monkeypatch):
    with remote_window(tmp_path, monkeypatch) as (_service, window):
        attach_service_connection(window)
        future = Future()
        monkeypatch.setattr(window.api, "force_stop_service", lambda: future)
        window.close_choice.refused("3 个会话、3 项待核对")
        assert FORCE_STOP in notice_text(window)
        dismiss_notice(window)
        window.service_connection.force_action.trigger()
        click(window.close_choice.force.dialog, FORCE_STOP)
        future.set_exception(ValueError("原服务进程身份不匹配"))
        wait_until(lambda: "错误 · 强制停止未完成" in notice_text(window))
        assert "身份不匹配" in notice_text(window)
        assert not window.lifecycle.closing and not window.close_choice.busy
        dismiss_notice(window)
        window.service_connection.force_action.trigger()
        assert window.close_choice.force.dialog is not None
        click(window.close_choice.force.dialog, CANCEL)


def test_old_normal_receipt_cannot_affect_retry_after_failed_force_stop(tmp_path, monkeypatch):
    with remote_window(tmp_path, monkeypatch) as (_service, window):
        attach_service_connection(window)
        endings = []
        future = Future()
        monkeypatch.setattr(window.service_connection.ending, "end",
            lambda token, done, failed: endings.append((done, failed)))
        monkeypatch.setattr(window.api, "force_stop_service", lambda: future)
        window.close_choice.stop_everything()
        window.close_choice.force.confirm()
        click(window.close_choice.force.dialog, FORCE_STOP)
        future.set_exception(ValueError("test refusal"))
        wait_until(lambda: not window.close_choice.busy)
        dismiss_notice(window)
        window.close_choice.stop_everything()
        assert len(endings) == 2 and window.close_choice.busy
        endings[0][1](ValueError("stale close receipt"))
        assert window.close_choice.busy and not window.close_choice.errors
        assert not notice_text(window)


def test_real_window_stops_unresponsive_service_and_keeps_other_project(tmp_path, monkeypatch):
    import sico.service.service_force_stop as stopping

    monkeypatch.setattr(stopping, "GRACE_SECONDS", .15)
    projects = [tmp_path / name for name in ("target", "other")]
    for project in projects:
        project.mkdir()
    with service_process(projects[0]) as (process, descriptor), (
            service_process(projects[1])) as (sibling, _other), ExitStack() as stack:
        api, token = open_demo(stack, descriptor, projects[0])
        window = window_for(stack, api, token)
        os.kill(process.pid, signal.SIGSTOP)
        window.service_connection.force_action.trigger()
        click(window.close_choice.force.dialog, FORCE_STOP)
        wait_until(lambda: window.lifecycle.closing, timeout=5)
        assert process.wait(timeout=2) == -signal.SIGKILL
        assert sibling.poll() is None
        assert (projects[0] / ".sico/ai/agent/sessions/live/events.jsonl").is_file()
