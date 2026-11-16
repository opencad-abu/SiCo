"""Identity damage, durable acknowledgements, cancellation and restart lifecycle guards."""

import json
import threading
from contextlib import ExitStack

import pytest
from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service
from agent_service_recovery_fixtures import client, restore, seed
from agent_worker_fixtures import heartbeat
from PyQt5.QtWidgets import QLabel

from sico.service.recovery_facts import read_facts
from sico.service.service_messages import ServiceRequest
from sico.storage.journal import SessionJournal
from sico.storage.project_files import write_record
from sico.storage.session_snapshot import fingerprint
from sico.transport.framing import ProtocolError


@pytest.mark.parametrize("damage", ["revision", "project", "input", "event", "symlink", "large"])
def test_damaged_evidence_is_protocol_error_and_never_creates_runtime(
        tmp_path, monkeypatch, damage):
    seed(tmp_path, monkeypatch, status="queued")
    directory = tmp_path / ".sico/ai/agent/sessions/old"
    snapshot = directory / "session.snapshot.json"
    row = json.loads(snapshot.read_text())
    if damage in {"revision", "project"}:
        row["project_id"] = "f" * 32
        if damage == "project":
            row["revision"] = fingerprint({k: v for k, v in row.items() if k != "revision"})
        write_record(snapshot, row)
    elif damage == "input":
        path = directory / "inbox/original.json"
        value = json.loads(path.read_text())
        value["service_address"]["project_id"] = "f" * 32
        write_record(path, value)
    elif damage == "event":
        (directory / "events.jsonl").write_text('{"invalid": true}\n')
    elif damage == "symlink":
        moved = snapshot.with_suffix(".moved")
        snapshot.rename(moved)
        snapshot.symlink_to(moved)
    else:
        snapshot.write_bytes(b"x" * 17000)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        with pytest.raises(ProtocolError):
            api.recovery.inspect("old").result(5)
        assert not service.owner.controllers
        wait_until(lambda: not service.dispatch.frontend.recovery._scanning)
        assert service.dispatch.frontend.recovery.pending
        assert service.dispatch.pending_work().empty


def test_slow_recovery_commit_has_no_early_runtime_or_ack_and_listener_stays_responsive(
        tmp_path, monkeypatch):
    seed(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    widget = QLabel("heartbeat")
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        view = api.recovery.inspect("old").result(5)
        original = SessionJournal.append_session
        def paused(journal, kind, *args):
            if kind == "session.recovered":
                entered.set()
                assert release.wait(8)
            return original(journal, kind, *args)
        monkeypatch.setattr(SessionJournal, "append_session", paused)
        receipt = api.recovery.restore(view, "paused-restore")
        try:
            wait_until(entered.is_set)
            heartbeat(widget)
            assert not receipt.done() and not service.owner.controllers and not release.is_set()
            status = api._admin.request(ServiceRequest()).result(3).status
            assert status.pending.jobs and status.pending.sessions == 1
            receipt.cancel()
            release.set()
            wait_until(lambda: "old" in service.owner.controllers)
            assert service.owner.controllers["old"].paused and not service.provider.seen
            assert api.recovery.inspect("old", "paused-restore").result(5)["operation"]
        finally:
            release.set()
            widget.close()


def test_restart_scan_retains_history_and_explicit_abandonment_never_claims_undo(tmp_path, monkeypatch):
    seed(tmp_path, monkeypatch, status="queued")
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        wait_until(lambda: not service.dispatch.frontend.recovery._scanning)
        assert service.dispatch.frontend.recovery.pending
        assert service.dispatch.pending_work().empty
        restore(api)
        token = api.open_session("old").result(5)
        api.control.request(token, "acquire").result(5)
        api.command(token, "abandon_input", "original").result(5)
        api.command(token, "resume", expected_task="").result(5)
        assert not service.provider.seen and not read_facts(tmp_path, "old").inputs
        event = service.owner.controllers["old"].journal.events()[-2]
        assert event["kind"] == "session.input_abandoned"
        assert event["payload"]["external_outcome"] == "unconfirmed"


def test_end_result_after_restart_is_original_intent_with_cleanup_unconfirmed(
        tmp_path, monkeypatch):
    context, address = seed(tmp_path, monkeypatch)
    with SessionJournal(tmp_path, "old") as journal:
        journal.append_session("session.end_requested", dict(address=address,
                               operation_id="end-original"), context)
        journal.append_session("session.end_observed", dict(address=address,
                               operation_id="end-original", state="drained"), context)
    before = (tmp_path / ".sico/ai/agent/sessions/old/events.jsonl").read_bytes()
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        result = api.recovery.end_result(address, "end-original").result(5)
        assert result.state == "needs_reconcile"
        assert api.recovery.end_result(address, "missing").result(5).state == "unknown"
        assert api.recovery.inspect("old").result(5)["state"] == "ended"
        with pytest.raises(ValueError):
            restore(api)
        assert not service.owner.controllers
        assert (tmp_path / ".sico/ai/agent/sessions/old/events.jsonl").read_bytes() == before
