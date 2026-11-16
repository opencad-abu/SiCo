"""Explicit restoration preserves original inputs, configuration and control boundaries."""

from contextlib import ExitStack

import pytest
from agent_service_business_fixtures import business_service
from agent_service_recovery_fixtures import client, restore, seed

from sico.service.recovery_facts import read_facts
from sico.storage.journal import SessionJournal
from sico.storage.project_files import write_record
from sico.transport.framing import ProtocolError


def test_recovery_stages_original_input_then_requires_control_and_explicit_resume(
        tmp_path, monkeypatch):
    context, address = seed(tmp_path, monkeypatch, status="queued")
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        view = api.recovery.inspect("old").result(5)
        assert view["state"] == "ready" and not service.owner.controllers
        restored = restore(api, view)
        controller = service.owner.controllers["old"]
        assert restored["state"] == "live" and controller.current == context
        assert controller.runtime_id != address["runtime_id"]
        assert controller.paused and not controller.inbox.pending and not service.provider.seen
        token = api.open_session("old").result(5)
        assert not api.can_control(token)
        with pytest.raises(ValueError):
            api.command(token, "recover_input", "original").result(5)
        api.control.request(token, "acquire").result(5)
        with pytest.raises(ValueError, match="逐项"):
            api.command(token, "resume", expected_task="").result(5)
        api.command(token, "recover_input", "original").result(5)
        api.command(token, "recover_input", "original").result(5)
        assert len(controller.inbox.pending) == 1 and not service.provider.seen
        api.command(token, "resume", expected_task="").result(5)
        assert controller.wait(5) and service.provider.seen == ["captured input"]
        receipt = api.recovery.input_result(address, "original").result(5).status
        assert receipt.outcome == "accepted" and receipt.execution == "completed"
        assert receipt.address.record() == address


@pytest.mark.parametrize("status", ["executing", "tool", "approval"])
def test_interrupted_execution_cannot_be_selected_or_replayed(tmp_path, monkeypatch, status):
    seed(tmp_path, monkeypatch, status=status)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        restore(api)
        token = api.open_session("old").result(5)
        api.control.request(token, "acquire").result(5)
        controller = service.owner.controllers["old"]
        with pytest.raises(ValueError, match="不能重放"):
            api.command(token, "recover_input", "original").result(5)
        if status == "tool":
            assert controller._task["status"] == "needs_reconcile"
            with pytest.raises(ValueError):
                api.command(token, "resume", expected_task="old-task").result(5)
            with pytest.raises(ValueError, match="未核对"):
                api.command(token, "acknowledge_interrupted", expected_task="old-task").result(5)
            api.command(token, "resolve_interrupted", "call", "completed",
                        expected_task="old-task", evidence="read-only fixture receipt").result(5)
            api.command(token, "resume", expected_task="old-task").result(5)
        else:
            assert controller._task["status"] == "cancelled"
            api.command(token, "resume", expected_task="old-task").result(5)
            assert not controller.paused
        assert not service.provider.seen and not controller.inbox.pending
        assert controller._task["status"] == "cancelled"


def test_credentials_wait_without_backend_and_do_not_overwrite_snapshot(tmp_path, monkeypatch):
    config = dict(provider="openai", base_url="https://example.test/v1", model="old-model",
                  api_key_env="MODEL_SECRET")
    seed(tmp_path, monkeypatch, config=config,
         environment={"MODEL_SECRET": "first-secret", "PATH": "/captured", "RANDOM": "hidden"})
    directory = tmp_path / ".sico/ai/agent/sessions/old"
    before = (directory / "session.snapshot.json").read_bytes()
    assert b"first-secret" not in before and b"hidden" not in before
    write_record(tmp_path / ".sico/ai/agent-provider.json", dict(config, model="new-model"))
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        view = api.recovery.inspect("old").result(5)
        assert view["state"] == "waiting_credentials"
        assert restore(api, view)["state"] == "waiting_credentials"
        assert not service.owner.controllers
        with pytest.raises(ProtocolError):
            restore(api, view, credentials={"WRONG": "key"})
        restored = restore(api, view, credentials={"MODEL_SECRET": "replacement-secret"})
        assert restored["state"] == "live"
        deps = service.owner.dependencies_for("old")
        assert deps.provider_config["model"] == "old-model"
        assert deps.environment == {"PATH": "/captured", "MODEL_SECRET": "replacement-secret"}
        assert (directory / "session.snapshot.json").read_bytes() == before
        assert b"replacement-secret" not in (directory / "events.jsonl").read_bytes()


def test_lost_recovery_receipt_uses_original_operation_and_never_recreates_runtime(
        tmp_path, monkeypatch):
    seed(tmp_path, monkeypatch)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        view = api.recovery.inspect("old").result(5)
        first = restore(api, view, "recover-original")
        second = restore(api, view, "recover-original")
        assert first["session"]["address"] == second["session"]["address"]
        observed = api.recovery.inspect("old", "recover-original").result(5)
        assert observed["operation"] == first["operation"]
        with pytest.raises(ValueError):
            restore(api, view, "different-operation")
        assert not service.provider.seen


def test_changed_evidence_refuses_recovery_and_partial_tail_inspection_is_readonly(
        tmp_path, monkeypatch):
    context, _address = seed(tmp_path, monkeypatch)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        view = api.recovery.inspect("old").result(5)
        with SessionJournal(tmp_path, "old") as journal:
            journal.append_session("session.note", {}, context)
        with pytest.raises(ValueError, match="证据已变化"):
            restore(api, view)
        path = tmp_path / ".sico/ai/agent/sessions/old/events.jsonl"
        with path.open("ab") as stream:
            stream.write(b'{"partial":')
        before = path.read_bytes()
        fresh = api.recovery.inspect("old").result(5)
        assert fresh["state"] == "ready" and path.read_bytes() == before
        assert not service.owner.controllers
        assert read_facts(tmp_path, "old").version == fresh["version"]
