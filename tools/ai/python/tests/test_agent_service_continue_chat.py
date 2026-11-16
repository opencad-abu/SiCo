"""Continue is one operation: credentials, interrupted work, dead host and UI races."""

import threading
from contextlib import ExitStack

import pytest
from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service, open_session
from agent_service_recovery_fixtures import client, seed
from test_agent_service_continue_ended import ended
from test_agent_service_recovery_gui import window_for

from sico.core.contracts import ToolCall
from sico.service.recovery_facts import read_facts
from sico.service.session_recovery import SessionRecovery


@pytest.mark.parametrize("status", [None, "queued", "tool", "approval"])
@pytest.mark.parametrize("offline", [False, True])
def test_continue_never_replays_prior_work_and_dead_host_does_not_block_chat(
        tmp_path, monkeypatch, status, offline):
    context, _ = seed(tmp_path, monkeypatch, status=status)
    if offline:
        def unavailable(*args):
            raise ConnectionRefusedError("old host has gone")
        monkeypatch.setattr(SessionRecovery, "_bridge", unavailable)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        token = api.recovery.continue_session("old").result(5)
        controller = service.owner.controllers["old"]
        assert api.can_control(token) and not controller.inbox.pending
        if status == "tool":
            assert controller.paused and controller._task["status"] == "needs_reconcile"
            assert read_facts(tmp_path, "old").inputs
            api.command(token, "resolve_interrupted", "call", "completed",
                        expected_task="old-task",
                        evidence="verified read-only fixture").result(5)
        elif status == "approval":
            assert controller._task["status"] == "cancelled"
            api.command(token, "resume", expected_task="old-task").result(5)
        assert not controller.paused
        assert controller.current == context and not service.provider.seen
        assert not read_facts(tmp_path, "old").inputs
        assert api.recovery.continue_session("old").result(5) == token
        if offline:
            result = controller.loop.tools.execute(ToolCall("probe", "get_context", {}), context)
            assert result.status == "router_unavailable"
        api.command(token, "submit", "new message").result(5)
        wait_until(lambda: not controller.busy)
        assert controller._task["status"] == "completed"
        assert service.provider.seen == ["new message"]


def test_unconfirmed_end_keeps_external_evidence_but_allows_chat(tmp_path, monkeypatch):
    ended(tmp_path, monkeypatch, released=False)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        token = api.recovery.continue_session("old").result(5)
        assert api.can_control(token)
        assert not (tmp_path / ".sico/ai/agent/sessions/old/end-releases").exists()
        api.command(token, "submit", "continue the conversation").result(5)
        wait_until(lambda: service.provider.seen == ["continue the conversation"])


@pytest.mark.parametrize("matching", [True, False])
def test_credentials_follow_matching_endpoint_and_are_never_written(tmp_path, monkeypatch, matching):
    config = dict(provider="openai", base_url="https://model.example/v1", model="test",
                  api_key_env="SICO_API_KEY")
    seed(tmp_path, monkeypatch, config=config)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        service.owner._default_config = dict(config, base_url=(config["base_url"] if matching
                                                     else "https://other.example/v1"))
        service.owner._default_environment = {"SICO_API_KEY": "private-continue-key"}
        api = client(stack, service, tmp_path)
        if matching:
            token = api.recovery.continue_session("old").result(5)
            assert api.can_control(token)
            assert service.owner.dependencies_for("old").environment["SICO_API_KEY"] == (
                "private-continue-key")
        else:
            with pytest.raises(ValueError, match="模型连接"):
                api.recovery.continue_session("old").result(5)
            assert not service.owner.controllers
        for path in (tmp_path / ".sico").rglob("*"):
            if path.is_file():
                assert b"private-continue-key" not in path.read_bytes()


def test_late_continue_does_not_switch_page_or_replace_new_draft(tmp_path, monkeypatch):
    seed(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    original = SessionRecovery.continue_session
    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(8)
        return original(*args, **kwargs)
    monkeypatch.setattr(SessionRecovery, "continue_session", delayed)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service, "other")
        api = client(stack, service, tmp_path)
        token = api.open_session("other").result(5)
        window = window_for(stack, api, token)
        window.navigation._continue_session("old")
        try:
            wait_until(entered.is_set)
            window.page.activate_session("other")
            wait_until(lambda: not window.page.opening)
            window.input.setPlainText("keep this draft")
            release.set()
            wait_until(lambda: "old" in service.owner.controllers)
            assert window.page.session.session_id == "other"
            assert window.input.toPlainText() == "keep this draft"
            assert window.service_connection.recovery.dialog is None
        finally:
            release.set()


def test_current_history_row_still_offers_continue(tmp_path, monkeypatch):
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QMenu
    from sico_ui import navigation_menu

    seed(tmp_path, monkeypatch)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        frontend = api.recovery.open_history("old").result(5)
        window = window_for(stack, api, frontend.session)
        def row():
            window.navigation.refresh()
            return next((item for item in window.navigation._session_items()
                         if item.data(0, Qt.UserRole) == "old"), None)
        wait_until(lambda: row() is not None)
        class Menu(QMenu):
            def exec_(self, *_args):
                action = next(a for a in self.actions() if a.text() == "继续")
                assert action.isEnabled()
                return action
        monkeypatch.setattr(navigation_menu, "QMenu", Menu)
        item = row()
        window.navigation._menu(item.treeWidget().visualItemRect(item).center(), item.treeWidget())
        wait_until(lambda: window.page.session.runtime_id is not None and not window.page.opening)
        assert api.can_control(window.page.session)
