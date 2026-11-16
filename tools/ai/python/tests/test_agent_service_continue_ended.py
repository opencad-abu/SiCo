"""Explicit continuation reopens released runtimes without reviving prior work."""

from contextlib import ExitStack

import pytest
from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service
from agent_service_recovery_fixtures import client, restore, seed

from sico.core.contracts import RunState
from sico.service.end_evidence import end_state
from sico.service.recovery_facts import read_facts
from sico.service.service_session_dto import SessionAddress
from sico.storage.journal import SessionJournal
from sico.storage.project_files import write_record
from sico.storage.session_release import save_release


def ended(project, monkeypatch, *, legacy=False, released=True):
    context, address = seed(project, monkeypatch)
    with SessionJournal(project, "old") as journal:
        journal.append_session("session.end_requested", dict(address=address,
                               operation_id="first-end"), context)
        event = journal.append_session("session.end_observed", dict(address=address,
            operation_id="first-end", state="drained"), context)
        directory = journal.directory
    if released:
        if legacy:
            write_record(directory / "end-release.json", dict(version=1, address=address,
                operation_id="first-end", sequence=event["sequence"]))
        else:
            save_release(directory, SessionAddress.from_record(address), "first-end",
                         event["sequence"])
    return context, address


@pytest.mark.parametrize("second_end", ["first-end", "second-end"])
@pytest.mark.parametrize("legacy", [False, True])
def test_released_history_continues_and_end_receipts_survive_multiple_runtimes(
        tmp_path, monkeypatch, legacy, second_end):
    context, address = ended(tmp_path, monkeypatch, legacy=legacy)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        view = api.recovery.inspect("old").result(5)
        assert view["state"] == "ready"
        assert not service.owner.controllers
        restored = restore(api, view)
        controller = service.owner.controllers["old"]
        assert controller.paused and not controller.inbox.pending and not service.provider.seen
        assert controller.current == context and controller.runtime_id != address["runtime_id"]
        assert not read_facts(tmp_path, "old").ends
        assert api.recovery.end_result(address, "first-end").result(5).state == "ended"
        repeated = restore(api, view, restored["operation"]["operation_id"])
        assert repeated["session"] == restored["session"]
        token = api.open_session("old").result(5)
        api.control.request(token, "acquire").result(5)
        api.command(token, "resume", expected_task="").result(5)
        api.command(token, "submit", "continue with a new task").result(5)
        assert controller.wait(5) and service.provider.seen == ["continue with a new task"]
        second_address = api.address(token).record()
        api.end_session(token, second_end).result(5)
        wait_until(lambda: "old" not in service.owner.controllers)
        assert api.recovery.inspect("old").result(5)["state"] == "ready"
        for captured, operation in ((address, "first-end"), (second_address, second_end)):
            assert api.recovery.end_result(captured, operation).result(5).state == "ended"
        directory = tmp_path / ".sico/ai/agent/sessions/old"
        (directory / "end-releases" /
         (second_address["runtime_id"] + "." + second_end + ".json")).unlink()
        assert api.recovery.inspect("old").result(5)["state"] == "ended"
        assert end_state(tmp_path, SessionAddress.from_record(second_address),
                         second_end) == "needs_reconcile"
        assert api.recovery.end_result(address, "first-end").result(5).state == "ended"


@pytest.mark.parametrize("reason", ["unreleased", "deleted"])
def test_unreleased_or_deleted_history_cannot_create_a_runtime(tmp_path, monkeypatch, reason):
    context, _ = ended(tmp_path, monkeypatch, released=reason != "unreleased")
    if reason == "deleted":
        with SessionJournal(tmp_path, "old") as journal:
            journal.append("codex.thread.deleted", {}, journal.state or RunState(context))
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        view = api.recovery.inspect("old").result(5)
        assert view["state"] == "ended"
        with pytest.raises(ValueError):
            restore(api, view)
        assert not service.owner.controllers and not service.provider.seen


def test_previous_end_does_not_hide_work_interrupted_after_continuation(tmp_path, monkeypatch):
    context, address = ended(tmp_path, monkeypatch)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        restore(api)
        service.owner.close_session("old")
        with SessionJournal(tmp_path, "old") as journal:
            state = RunState(context)
            state.task = dict(id="continued-task", status="executing", pending_calls=[])
            journal.append("task.started", dict(text="continued work", context=context.record()), state)
        facts = read_facts(tmp_path, "old")
        assert not facts.ends and facts.unresolved
        assert api.recovery.inspect("old").result(5)["state"] == "ready"
        assert api.recovery.end_result(address, "first-end").result(5).state == "ended"


def test_ended_row_continue_is_ready_to_send_even_with_a_cached_retired_token(tmp_path, monkeypatch):
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QMenu
    from agent_service_gui_fixtures import ServiceTestWindow
    from sico.service.frontend_session import FrontendSession
    from sico_ui import navigation_menu
    from sico_ui.service_connection import attach_service_connection

    ended(tmp_path, monkeypatch)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        restore(api)
        token = api.open_session("old").result(5)
        api.control.request(token, "acquire").result(5)
        window = ServiceTestWindow(FrontendSession(api, token))
        stack.callback(window.deleteLater)
        stack.callback(window.request_quit)
        attach_service_connection(window)
        window.show()
        wait_until(lambda: not window.page.opening and window.presentation.state is not None)
        window.service_connection.ending.request(token)
        wait_until(lambda: "old" not in service.owner.controllers and api.is_closing(token))

        def row():
            window.navigation.refresh()
            return next((item for item in window.navigation._session_items()
                         if item.data(0, Qt.UserRole) == "old"
                         and "（已结束）" in item.text(0)), None)

        wait_until(lambda: row() is not None)
        class Menu(QMenu):
            def exec_(self, *_args):
                return next(action for action in self.actions() if action.text() == "继续")
        monkeypatch.setattr(navigation_menu, "QMenu", Menu)
        item = row()
        tree = item.treeWidget()
        window.navigation._menu(tree.visualItemRect(item).center(), tree)
        panel = window.service_connection.recovery
        wait_until(lambda: "old" in service.owner.controllers and not window.page.opening
                   and window.page.session.runtime_id != token.runtime_id
                   and window.input.isEnabled())
        assert panel.dialog is None and not panel.action.isVisible()
        assert window.input.hasFocus() and api.can_control(window.page.session)
        assert not service.owner.controllers["old"].paused
        assert service.owner.controllers["old"].runtime_id != token.runtime_id
        assert not service.provider.seen
        window.input.setPlainText("continue directly")
        wait_until(lambda: window.send.isEnabled())
        window.send.click()
        wait_until(lambda: service.provider.seen == ["continue directly"])
        wait_until(lambda: any(message.get("text") == "continue directly"
                               for message in window.presentation.transcript.messages))
        wait_until(lambda: "done" in window.display.toPlainText())


def test_preview_then_continue_replays_new_bubbles_in_the_open_page(tmp_path, monkeypatch):
    from agent_gui_wait import wait_until
    from agent_service_business_fixtures import business_service, open_session
    from agent_service_recovery_fixtures import client
    from test_agent_service_recovery_gui import window_for

    ended(tmp_path, monkeypatch)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service, "other")
        api = client(stack, service, tmp_path)
        token = api.open_session("other").result(5)
        window = window_for(stack, api, token)
        window.page.preview_session("old")
        wait_until(lambda: window.page.reviewing == "old" and not window.page.opening)
        window.navigation._continue_session("old")
        wait_until(lambda: window.page.reviewing is None
                   and window.page.session.session_id == "old"
                   and not window.page.opening and window.input.isEnabled())
        window.input.setPlainText("继续")
        wait_until(window.send.isEnabled)
        window.send.click()
        wait_until(lambda: service.provider.seen == ["继续"])
        wait_until(lambda: any(message.get("text") == "继续"
                               for message in window.presentation.transcript.messages))
        wait_until(lambda: "done" in window.display.toPlainText())
