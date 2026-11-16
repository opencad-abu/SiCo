"""Deletion intent and durable completion survive races and lost presentation updates."""

from concurrent.futures import Future
from contextlib import ExitStack

import pytest
from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service
from agent_service_recovery_fixtures import client
from sico.core.contracts import RunState
from sico.service.recovery_facts import read_facts
from sico.storage.history import SessionReader
from sico.storage.journal import SessionJournal
from test_agent_service_continue_ended import ended
from test_agent_service_record_review import startup_failure


def test_normal_delete_uses_the_reviewed_revision(tmp_path, monkeypatch):
    context, _ = ended(tmp_path, monkeypatch)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        review = api.review_deletion("old").result(5)
        assert not review["requires_review"]
        with SessionJournal(tmp_path, "old") as journal:
            journal.append_session("session.control", {}, context)
        with pytest.raises(ValueError, match="记录已变化"):
            api.delete_session("old", review_version=review["version"]).result(5)
        assert not read_facts(tmp_path, "old").deleted
        review = api.review_deletion("old").result(5)
        api.delete_session("old", review_version=review["version"]).result(5)
        assert read_facts(tmp_path, "old").deleted


def test_retry_after_committed_delete_refreshes_catalog(tmp_path, monkeypatch):
    ended(tmp_path, monkeypatch)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        catalog = service.dispatch.frontend.workspace.catalog
        original = catalog.request
        calls = []
        def request():
            calls.append(True)
            if len(calls) == 1:
                result = Future()
                result.set_exception(RuntimeError("catalog refresh interrupted"))
                return result
            return original()
        monkeypatch.setattr(catalog, "request", request)
        with pytest.raises(RuntimeError):
            api.delete_session("old").result(5)
        assert read_facts(tmp_path, "old").deleted
        api.delete_session("old").result(5)
        assert len(calls) == 2
        assert catalog.snapshot()["availability"]["old"] == "已删除"


def test_reviewed_delete_retry_retires_pending_projection(tmp_path, monkeypatch):
    startup_failure(tmp_path, monkeypatch, ended=False)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        recovery = service.dispatch.frontend.recovery
        wait_until(lambda: recovery.pending == 1)
        review = api.review_deletion("old").result(5)
        original = recovery.retired
        def interrupted(_):
            raise RuntimeError("projection interrupted")
        monkeypatch.setattr(recovery, "retired", interrupted)
        with pytest.raises(RuntimeError):
            api.delete_session("old", review_version=review["version"]).result(5)
        assert read_facts(tmp_path, "old").deleted
        monkeypatch.setattr(recovery, "retired", original)
        api.delete_session("old", review_version=review["version"]).result(5)
        assert recovery.pending == 0
        events = SessionReader(tmp_path / ".sico/ai/agent", "old").events()
        assert sum(row["kind"] == "codex.thread.deleted" for row in events) == 1


@pytest.mark.parametrize("action", ["delete", "rename"])
@pytest.mark.parametrize("payload", [{}, {"thread_id": ""}, {"thread_id": None},
                                     {"thread_id": "  "}, {"thread_id": 42}])
def test_malformed_native_thread_does_not_start_a_replacement(
        tmp_path, monkeypatch, payload, action):
    context, _ = ended(tmp_path, monkeypatch)
    with SessionJournal(tmp_path, "old") as journal:
        journal.append("codex.thread", payload, journal.state or RunState(context))
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        calls = []
        def backend(*_args, **_kwargs):
            calls.append(True)
            raise RuntimeError("invalid identity cannot start backend")
        monkeypatch.setattr("sico.service.session_records.create_backend", backend)
        with pytest.raises(ValueError):
            (api.delete_session("old") if action == "delete" else
             api.rename_session("old", "Damaged record")).result(5)
        assert not calls
        # Malformed evidence is retained even if it cannot pass recovery validation.
        assert not any(row["kind"] == "codex.thread.deleted" for row in
                       SessionReader(tmp_path / ".sico/ai/agent", "old").events())
