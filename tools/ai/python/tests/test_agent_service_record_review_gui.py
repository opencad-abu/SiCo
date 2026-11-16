"""History deletion reviews startup failure evidence on Qt without replaying work."""

from concurrent.futures import Future

import pytest
from agent_gui_wait import wait_until
from agent_service_gui_fixtures import remote_window
from sico.service.recovery_facts import read_facts
from sico.storage.journal import SessionJournal
from sico_ui.service_connection import attach_service_connection
from test_agent_service_continue_ended import ended as ended_record
from test_agent_service_record_review import startup_failure


@pytest.mark.parametrize("ended", [True, False])
@pytest.mark.parametrize("choice", ["delete", "cancel", "changed"])
def test_history_delete_presents_review_and_rechecks_confirmation(
        tmp_path, monkeypatch, choice, ended):
    context, _ = startup_failure(tmp_path, monkeypatch, ended=ended, legacy_input=not ended)
    with remote_window(tmp_path, monkeypatch) as (service, window):
        attach_service_connection(window)
        shown, errors = [], []
        def confirm(parent, title, message, **options):
            shown.append((title, message, options))
            if choice == "changed":
                with SessionJournal(tmp_path, "old") as journal:
                    journal.append_session("session.control", {}, context)
            return choice != "cancel"
        monkeypatch.setattr("sico_ui.navigation_actions.confirm", confirm)
        monkeypatch.setattr(window.navigation, "_alert", lambda message, *_: errors.append(message))
        window.navigation._delete("old")
        wait_until(lambda: bool(shown))
        title, message, options = shown[0]
        assert title == "核对后删除" and options["choice"] == "放弃并删除"
        assert "1 条未决输入" in message and "不会重发输入" in message
        if choice == "delete":
            wait_until(lambda: read_facts(tmp_path, "old").deleted)
        elif choice == "changed":
            wait_until(lambda: bool(errors))
            assert "记录已变化" in errors[-1]
        assert read_facts(tmp_path, "old").deleted == (choice == "delete")
        assert "old" not in service.owner.controllers and not service.provider.seen


def test_delete_review_completion_after_page_change_does_not_confirm(tmp_path, monkeypatch):
    startup_failure(tmp_path, monkeypatch)
    with remote_window(tmp_path, monkeypatch) as (_, window):
        attach_service_connection(window)
        review = window.api.review_deletion("old").result(5)
        pending = Future()
        monkeypatch.setattr(window.api, "review_deletion", lambda _: pending)
        monkeypatch.setattr("sico_ui.navigation_actions.confirm",
                            lambda *_a, **_kw: pytest.fail("stale review must not confirm"))
        window.navigation._delete("old")
        window.page.activation += 1
        pending.set_result(review)
        window.actions.commands.poll()
        assert not read_facts(tmp_path, "old").deleted


@pytest.mark.parametrize("choice", ["delete", "cancel", "changed"])
def test_normal_history_delete_binds_confirmation_to_record_version(tmp_path, monkeypatch, choice):
    context, _ = ended_record(tmp_path, monkeypatch)
    with remote_window(tmp_path, monkeypatch) as (_, window):
        attach_service_connection(window)
        shown, errors = [], []
        def confirm(parent, title, message, **options):
            shown.append((title, options["choice"]))
            if choice == "changed":
                with SessionJournal(tmp_path, "old") as journal:
                    journal.append_session("session.control", {}, context)
            return choice != "cancel"
        monkeypatch.setattr("sico_ui.navigation_actions.confirm", confirm)
        monkeypatch.setattr(window.navigation, "_alert", lambda message, *_: errors.append(message))
        window.navigation._delete("old")
        wait_until(lambda: bool(shown))
        assert shown == [("删除会话", "删除")]
        if choice == "delete":
            wait_until(lambda: read_facts(tmp_path, "old").deleted)
        elif choice == "changed":
            wait_until(lambda: bool(errors))
            assert "记录已变化" in errors[-1]
        assert read_facts(tmp_path, "old").deleted == (choice == "delete")
