"""Only explicitly reviewed startup failures can retire unresolved history."""

from contextlib import ExitStack

import pytest
from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service
from agent_service_recovery_fixtures import client, seed
from sico.service.end_evidence import end_state
from sico.service.recovery_facts import read_facts
from sico.service.service_session_dto import SessionAddress
from sico.storage.journal import SessionJournal


def startup_failure(project, monkeypatch, extra=None, *, ended=True, legacy_input=False):
    context, address = seed(project, monkeypatch, status="executing")
    with SessionJournal(project, "old") as journal:
        state = journal.state
        if extra:
            journal.append(*extra, state)
        journal.append("codex.turn.settings", dict(thread_id=None, status="not_sent"), state)
        state.task["status"] = "needs_reconcile"
        journal.append("task.needs_reconcile", dict(message="Codex backend unavailable"), state)
        if ended:
            journal.append_session("session.end_requested", dict(address=address,
                                   operation_id="old-end"), context)
            journal.append_session("session.end_observed", dict(address=address,
                operation_id="old-end", state="needs_reconcile"), context)
        if legacy_input:
            import json

            from sico.storage.project_files import write_record

            path = journal.directory / "inbox/original.json"
            row = json.loads(path.read_text())
            row.pop("service_address", None)
            write_record(path, row)
    return context, address


@pytest.mark.parametrize("ended,legacy_input", [(True, False), (False, False), (False, True)])
def test_reviewed_startup_delete_preserves_evidence_and_does_not_replay(
        tmp_path, monkeypatch, ended, legacy_input):
    _, address = startup_failure(tmp_path, monkeypatch, ended=ended, legacy_input=legacy_input)
    directory = tmp_path / ".sico/ai/agent/sessions/old"
    original = (directory / "events.jsonl").read_bytes()
    inputs = {p.name: p.read_bytes() for p in (directory / "inbox").glob("*.json")}
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        wait_until(lambda: service.dispatch.frontend.recovery.pending == 1)
        with pytest.raises(ValueError, match="未决工作"):
            api.delete_session("old").result(5)
        review = api.review_deletion("old").result(5)
        assert review["requires_review"] and review["input_count"] == 1
        assert review["task_status"] == "needs_reconcile"
        assert (directory / "events.jsonl").read_bytes() == original
        monkeypatch.setattr("sico.service.session_records.create_backend",
                            lambda *_a, **_kw: pytest.fail("review must not open a backend"))
        api.delete_session("old", review_version=review["version"]).result(5)
        facts = read_facts(tmp_path, "old")
        assert facts.deleted and not facts.unresolved and not facts.resources_released
        observed = end_state(tmp_path, SessionAddress.from_record(address), "old-end")
        assert observed == ("needs_reconcile" if ended else "unknown")
        assert not service.owner.controllers and not service.provider.seen
        assert service.dispatch.frontend.recovery.pending == 0
        catalog = service.dispatch.frontend.workspace.catalog.snapshot()
        assert catalog["availability"]["old"] == "已删除"
        assert (directory / "events.jsonl").read_bytes().startswith(original)
        assert inputs == {p.name: p.read_bytes() for p in (directory / "inbox").glob("*.json")}
        from sico.storage.history import SessionReader

        deleted = SessionReader(directory.parent.parent, "old").events()[-1]
        assert deleted["payload"]["abandoned_inputs"] == ["original"]
        assert deleted["payload"]["external_outcome"] == "unconfirmed"
        assert bool(deleted["payload"]["original_end"]) == ended
        assert bool(facts.ends) == ended
        if not ended:
            assert not (directory / "end-releases").exists()
        api.delete_session("old", review_version=review["version"]).result(5)


@pytest.mark.parametrize("ended", [True, False])
@pytest.mark.parametrize("extra", [
    ("codex.connection", {"connection_id": "old-connection"}),
    ("codex.thread", {"thread_id": "native-thread"}),
    ("tool.started", {"id": "call", "name": "write_cellview"}),
    ("session.future_unknown_work", {}),
])
def test_review_cannot_bypass_dispatched_native_or_unknown_work(
        tmp_path, monkeypatch, extra, ended):
    startup_failure(tmp_path, monkeypatch, extra, ended=ended)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        facts = read_facts(tmp_path, "old")
        with pytest.raises(ValueError, match="不能通过核对删除"):
            api.review_deletion("old").result(5)
        with pytest.raises(ValueError, match="不能通过核对删除"):
            api.delete_session("old", review_version=facts.version).result(5)
        assert not read_facts(tmp_path, "old").deleted


@pytest.mark.parametrize("ended", [True, False])
@pytest.mark.parametrize("change", ["writer", "revision", "same_service"])
def test_review_rechecks_ownership_and_original_revision(tmp_path, monkeypatch, change, ended):
    context, address = startup_failure(tmp_path, monkeypatch, ended=ended)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        review = api.review_deletion("old").result(5)
        if change == "writer":
            stack.enter_context(SessionJournal(tmp_path, "old"))
        else:
            with SessionJournal(tmp_path, "old") as journal:
                if change == "revision":
                    journal.append_session("session.control", {}, context)
                else:
                    address["service_id"] = service.descriptor.service_id
                    journal.append_session("session.end_observed", dict(address=address,
                        operation_id="old-end", state="needs_reconcile"), context)
                    review = dict(review, version=read_facts(tmp_path, "old").version)
        with pytest.raises((ValueError, RuntimeError)):
            api.delete_session("old", review_version=review["version"]).result(5)
        assert not read_facts(tmp_path, "old").deleted


def test_review_does_not_allow_a_missing_release_receipt(tmp_path, monkeypatch):
    from test_agent_service_continue_ended import ended

    ended(tmp_path, monkeypatch, released=False)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        with pytest.raises(ValueError, match="不能通过核对删除"):
            api.review_deletion("old").result(5)
        assert not read_facts(tmp_path, "old").deleted


@pytest.mark.parametrize("change", ["live", "current_input", "ending", "missing_not_sent"])
@pytest.mark.parametrize("ended", [True, False])
def test_end_does_not_replace_ownership_or_startup_evidence(tmp_path, monkeypatch, change, ended):
    import json

    from sico.storage.project_files import write_record

    context, address = startup_failure(tmp_path, monkeypatch, ended=ended)
    directory = tmp_path / ".sico/ai/agent/sessions/old"
    if change == "missing_not_sent":
        path = directory / "events.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows = [row for row in rows if row["kind"] != "codex.turn.settings"]
        for sequence, row in enumerate(rows, 1):
            row["sequence"] = sequence
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        if change == "live":
            service.owner.open("old", {}, context)
        elif change == "current_input":
            path = directory / "inbox/original.json"
            row = json.loads(path.read_text())
            row["service_address"]["service_id"] = service.descriptor.service_id
            write_record(path, row)
        elif change == "ending":
            with SessionJournal(tmp_path, "old") as journal:
                journal.append_session("session.end_requested", dict(address=address,
                                       operation_id="old-end"), context)
        facts = read_facts(tmp_path, "old")
        with pytest.raises(ValueError):
            api.review_deletion("old").result(5)
        with pytest.raises(ValueError):
            api.delete_session("old", review_version=facts.version).result(5)
        assert not read_facts(tmp_path, "old").deleted


@pytest.mark.parametrize("ended", [True, False])
@pytest.mark.parametrize("change", ["accepted", "unconfirmed", "rejected", "thread",
                                    "pending_call", "second_task_unsent", "second_task_unknown"])
def test_review_requires_unsent_evidence_for_every_task(tmp_path, monkeypatch, ended, change):
    startup_failure(tmp_path, monkeypatch, ended=ended)
    with SessionJournal(tmp_path, "old") as journal:
        state = journal.state
        if change.startswith("second_task"):
            state.task["status"] = "cancelled"
            journal.append("task.cancelled", dict(message="First startup abandoned"), state)
            state.task = dict(id="second-task", status="executing", pending_calls=[])
            journal.append("task.started", dict(text="second input",
                                               context=state.context.record()), state)
            if change == "second_task_unsent":
                journal.append("codex.turn.settings",
                               dict(thread_id=None, status="not_sent"), state)
            state.task["status"] = "needs_reconcile"
            journal.append("task.needs_reconcile", {}, state)
        elif change == "pending_call":
            state.task["pending_calls"] = ["unconfirmed-call"]
            journal.append("task.needs_reconcile", {}, state)
        else:
            journal.append("codex.turn.settings", dict(
                thread_id="native-thread" if change == "thread" else None,
                status="not_sent" if change == "thread" else change), state)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        facts = read_facts(tmp_path, "old")
        if change == "second_task_unsent":
            review = api.review_deletion("old").result(5)
            api.delete_session("old", review_version=review["version"]).result(5)
            assert read_facts(tmp_path, "old").deleted
        else:
            with pytest.raises(ValueError, match="不能通过核对删除"):
                api.review_deletion("old").result(5)
            with pytest.raises(ValueError, match="不能通过核对删除"):
                api.delete_session("old", review_version=facts.version).result(5)
            assert not read_facts(tmp_path, "old").deleted
        assert not service.owner.controllers and not service.provider.seen
