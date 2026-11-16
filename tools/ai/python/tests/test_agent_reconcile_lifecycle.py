"""No navigation or legacy command may erase an unresolved operation."""

from contextlib import ExitStack

import pytest
from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service, open_session
from agent_service_recovery_fixtures import client, seed
from info_app_fixtures import dismiss_notice, notice_text
from sico.service.reconcile_calls import from_events
from sico.service.reconcile_cli import execute, inspect
from sico.service.remote_frontend import RemoteFrontend
from sico.storage.journal import SessionJournal
from test_agent_interrupted_reconcile import interrupt, status_tool
from test_agent_service_control import attach
from test_agent_service_control_gui import window_for


@pytest.mark.parametrize("calls", [0, 2])
def test_continue_after_restart_preserves_card_and_every_call(tmp_path, monkeypatch, calls):
    with business_service(tmp_path, monkeypatch) as service:
        open_session(service)
        task = interrupt(service.owner.controllers["live"], calls=calls)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = RemoteFrontend(service.descriptor, tmp_path, "live")
        stack.callback(api.close_desktop)
        token = api.recovery.continue_session("live").result(5)
        controller = service.owner.controllers["live"]
        assert controller._task["id"] == task and api.can_control(token)
        assert api.recovery.continue_session("live").result(5) == token
        assert len(from_events(controller.journal.events(), task).rows()) == calls
        if calls:
            assert controller.paused and controller._task["status"] == "needs_reconcile"
            assert not any(e["kind"] == "session.reconcile_acknowledged"
                           for e in controller.journal.events())
        else:
            assert not controller.paused and controller._task["status"] == "cancelled"
        assert not service.provider.seen


def test_direct_abandon_and_missing_evidence_cannot_clear_unknown_writes(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        controller = service.owner.controllers["live"]
        task = interrupt(controller, calls=2)
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        for operation in (lambda: controller.acknowledge_interrupted(),
                          lambda: api.command(token, "acknowledge_interrupted",
                                              expected_task=task).result(5),
                          lambda: api.command(token, "resolve_interrupted", "call-0",
                                              "completed", expected_task=task).result(5)):
            with pytest.raises(ValueError):
                operation()
        assert controller._task["status"] == "needs_reconcile"
        assert not from_events(controller.journal.events(), task).decisions
        api.command(token, "resolve_interrupted", "call-0", "completed", expected_task=task,
                    evidence="原请求回执显示 saved=true").result(5)
        api.command(token, "resolve_interrupted", "call-0", "completed", expected_task=task,
                    evidence="重复相同决定").result(5)
        with pytest.raises(ValueError, match="不同"):
            api.command(token, "resolve_interrupted", "call-0", "not_executed",
                        expected_task=task, evidence="conflict").result(5)
        assert controller.paused
        api.command(token, "resolve_interrupted", "call-1", "not_executed", expected_task=task,
                    evidence="原请求未执行；只读检查目标不存在").result(5)
        rows = [e for e in controller.journal.events() if e["kind"] in {
            "session.reconcile_decided", "session.reconcile_acknowledged"}]
        assert len(rows) == 3
        assert all(e["timestamp"] and e["payload"]["actor"] and e["payload"]["evidence"]
                   and e["payload"]["client_id"] != "local" for e in rows)
        assert not service.provider.seen
        terminal = next(e for e in controller.journal.events() if e["kind"] == "task.cancelled")
        assert rows[-1]["sequence"] < terminal["sequence"]


def test_no_calls_decision_survives_restart_with_audit(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        controller = service.owner.controllers["live"]
        task = interrupt(controller, calls=0)
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        api.command(token, "resolve_interrupted", "", "no_calls", expected_task=task).result(5)
    with SessionJournal(tmp_path, "live") as journal:
        assert journal.state.task["status"] == "cancelled"
        decisions = [e for e in journal.events() if e["kind"] == "session.reconcile_decided"]
        assert decisions[0]["payload"]["decision"] == "no_calls"
        assert "没有待核对" in decisions[0]["payload"]["evidence"]


def test_idle_attachment_reconnects_without_replaying_or_clearing_task(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        controller = service.owner.controllers["live"]
        task = interrupt(controller, calls=1)
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        window = window_for(stack, api, token)
        window.show()
        wait_until(lambda: window.reconcile_panel.isVisible())
        old_epoch = api._attach.epoch
        api._attach.retire_connection()
        wait_until(lambda: api._attach.epoch > old_epoch)
        wait_until(lambda: api.connection_state == "connected")
        wait_until(lambda: window.reconcile_panel.buttons["query"].isEnabled())
        assert controller._task["id"] == task and controller.paused
        assert not service.provider.seen
        assert not any(e["kind"] == "session.reconcile_acknowledged"
                       for e in controller.journal.events())


def test_business_unknown_keeps_real_reconcile_buttons_usable(tmp_path, monkeypatch):
    monkeypatch.setattr("sico_ui.reconcile_actions.decision_evidence", lambda *_a: "原请求只读回执")
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        controller = service.owner.controllers["live"]
        task = interrupt(controller, calls=1)
        observed = []
        status_tool(controller, observed)
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        window = window_for(stack, api, token)
        window.show()
        panel = window.reconcile_panel
        wait_until(lambda: panel.isVisible() and panel.buttons["query"].isEnabled())
        epoch, activation = api._commands.epoch, window.page.activation
        failures = []

        def unavailable():
            raise RuntimeError("Native runtime is unavailable")

        monkeypatch.setattr(controller.backend_commands, "resource_status", unavailable)
        window.actions.run_command("resource_status",
                                   failure=lambda exc: failures.append(exc) or False)
        wait_until(lambda: bool(failures))
        panel.refresh()
        assert api.connection_state == "connected" and api.can_control(token)
        assert api._commands.epoch == epoch and window.page.activation == activation
        assert panel.card["task_id"] == task and panel.buttons["completed"].isEnabled()
        panel.buttons["query"].click()
        wait_until(lambda: len(observed) == 1 and "原请求核对" in notice_text(window))
        dismiss_notice(window)
        panel.buttons["completed"].click()
        wait_until(lambda: not controller.paused)
        wait_until(lambda: "核对决定已记录" in notice_text(window))
        assert not service.provider.seen
        assert window.page.activation == activation


def test_archived_card_requires_continue_before_enabling_real_buttons(tmp_path, monkeypatch):
    from test_agent_service_recovery_gui import window_for as history_window

    with business_service(tmp_path, monkeypatch) as service:
        open_session(service)
        task = interrupt(service.owner.controllers["live"], calls=1)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = RemoteFrontend(service.descriptor, tmp_path, "live")
        stack.callback(api.close_desktop)
        frontend = api.recovery.open_history("live").result(5)
        window = history_window(stack, api, frontend.session)
        window.show()
        panel = window.reconcile_panel
        wait_until(lambda: panel.isVisible())
        assert all(not button.isEnabled() for button in panel.buttons.values())
        assert "右键选择“继续”" in panel.hint.text()
        panel.decide("query")
        assert "历史只读" in notice_text(window)
        dismiss_notice(window)
        window.navigation._continue_session("live")
        wait_until(lambda: window.page.session.runtime_id is not None and not window.page.opening)
        wait_until(lambda: panel.buttons["query"].isEnabled())
        assert "历史只读" not in window.windowTitle()
        assert panel.card["task_id"] == task
        assert not any(e["kind"] == "session.reconcile_acknowledged"
                       for e in service.owner.controllers["live"].journal.events())


def test_recorded_decision_can_retry_cleanup_from_real_buttons(tmp_path, monkeypatch):
    monkeypatch.setattr("sico_ui.reconcile_actions.decision_evidence",
                        lambda *_a: "原操作结果已只读核对")
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        controller = service.owner.controllers["live"]
        interrupt(controller, calls=1)
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        window = window_for(stack, api, token)
        window.show()
        panel = window.reconcile_panel
        wait_until(lambda: panel.isVisible())
        with monkeypatch.context() as failing:
            def fail():
                raise ValueError("工具仍在收尾，请等待回执")
            failing.setattr(controller.loop, "acknowledge_interrupted", fail)
            panel.buttons["completed"].click()
            wait_until(lambda: "仍在收尾" in notice_text(window))
            dismiss_notice(window)
            wait_until(lambda: panel.card["operations"][0].get("decision") == "completed")
            assert panel.buttons["completed"].isEnabled()
            assert not panel.buttons["not_executed"].isEnabled()
        panel.buttons["completed"].click()
        wait_until(lambda: not controller.paused)
        decisions = [e for e in controller.journal.events()
                     if e["kind"] == "session.reconcile_decided"]
        assert len(decisions) == 1


def test_cli_inspection_does_not_open_runtime_and_decision_uses_service(tmp_path, monkeypatch):
    seed(tmp_path, monkeypatch, status="tool")
    path = tmp_path / ".sico/ai/agent/sessions/old/events.jsonl"
    before = path.read_bytes()
    report = inspect(tmp_path, "old")
    assert report["task_id"] == "old-task" and report["operations"][0]["id"] == "call"
    assert path.read_bytes() == before
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        result = execute(api, "old", "old-task", "completed", "call", "原只读请求回执", 5)
        assert result["status"] == "cancelled"
        assert not service.provider.seen


def test_real_buttons_disconnect_feedback_and_repeatable_interruption(tmp_path, monkeypatch):
    monkeypatch.setattr("sico_ui.reconcile_actions.decision_evidence", lambda *_a: "只读检查证据")
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        controller = service.owner.controllers["live"]
        interrupt(controller, calls=1)
        observed = []
        status_tool(controller, observed)
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        window = window_for(stack, api, token)
        window.show()
        wait_until(lambda: window.reconcile_panel.isVisible())
        panel = window.reconcile_panel
        with monkeypatch.context() as disconnected:
            disconnected.setattr(RemoteFrontend, "connection_state",
                                 property(lambda _s: "disconnected"))
            panel.refresh()
            assert all(not b.isEnabled() for b in panel.buttons.values())
            assert "项目服务未连接" in panel.hint.text() and panel.reconnect.isVisible()
            panel.decide("query")
            assert "未连接" in notice_text(window) and not observed
            dismiss_notice(window)
        panel.refresh()
        panel.buttons["query"].click()
        wait_until(lambda: len(observed) == 1)
        wait_until(lambda: "原请求核对" in notice_text(window))
        dismiss_notice(window)
        panel.buttons["completed"].click()
        wait_until(lambda: not controller.paused)
        wait_until(lambda: "核对决定已记录" in notice_text(window))
        dismiss_notice(window)
        new_task = interrupt(controller, calls=0)
        assert controller._task["id"] == new_task
        wait_until(lambda: not panel.card and not panel.isVisible())
        assert controller.reconciliation.auto_acknowledge_empty()
        wait_until(lambda: not controller.paused)
        api.command(token, "submit", "next ordinary task").result(5)
        assert controller.wait(5) and service.provider.seen == ["next ordinary task"]
