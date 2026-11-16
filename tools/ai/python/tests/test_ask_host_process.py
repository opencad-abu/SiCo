"""The host worker separates durable admission from advisory wake and cleanup."""

import io
import json
from types import SimpleNamespace

import pytest
from agent_service_business_fixtures import business_service
from sico.service.frontend_ask import FrontendAsk
from sico.service.host_process import run_host_submit
from sico.transport.bridge_client import BridgeClient
from sico.transport.quick_codec import encode_text
from test_agent_service_host import host_bridge, target


def run_worker(monkeypatch, root, context, *, command="host-submit", output=None,
               sequence=None):
    row = dict(kind="submit", id=context.target_id, text_b64=encode_text("Ask"),
               context=context.record())
    if sequence is not None:
        row["ask_sequence"] = sequence
    raw = json.dumps(row).encode() + b"\n"
    output = output if output is not None else io.StringIO()
    monkeypatch.setattr("sys.stdin", SimpleNamespace(buffer=io.BytesIO(raw)))
    monkeypatch.setattr("sys.stdout", output)
    code = run_host_submit(SimpleNamespace(launch_dir=str(root), provider_config=None,
                                          command=command))
    return code, output.getvalue().splitlines()


@pytest.mark.parametrize("fault", ["none", "cleanup", "wake_write"])
@pytest.mark.parametrize("sequence", [None, 1])
def test_accepted_receipt_survives_advisory_failures_and_query_does_not_replay(
        tmp_path, monkeypatch, fault, sequence):
    context = target(tmp_path, "input")
    monkeypatch.setattr("sico.service.host_gateway.resolve_provider_config",
                        lambda *_args, **_kwargs: None)
    reports = []
    monkeypatch.setattr("sico.service.host_process.diagnostics.submit",
                        lambda _name, message, *_args: reports.append(message))
    if fault == "cleanup":
        def close(ask):
            ask._gateway.close()
            raise OSError("cleanup failed")
        monkeypatch.setattr(FrontendAsk, "close", close)

    class WakeFails(io.StringIO):
        def write(self, text):
            if text.startswith("@wake"):
                raise OSError("wake write failed")
            return super().write(text)

    with host_bridge(tmp_path, context) as (_, _client), business_service(
            tmp_path, monkeypatch, bridge_factory=BridgeClient) as service:
        code, lines = run_worker(monkeypatch, tmp_path, context,
                                 sequence=sequence,
                                 output=WakeFails() if fault == "wake_write" else None)
        assert code == 0 and lines[-1] == "@accepted input"
        owner, = service.owner.controllers.values()
        assert owner.wait(5)
        if fault != "wake_write":
            assert lines[0].split() == ["@wake", "input", owner.session_id,
                service.descriptor.service_id, owner.runtime_id] + (
                    [str(sequence), "input"] if sequence is not None else [])
        else:
            assert lines == ["@accepted input"]
        before = owner.journal.events()
        code, queried = run_worker(monkeypatch, tmp_path, context, command="host-query")
        assert code == 0 and queried[-1] == "@accepted input"
        assert owner.journal.events() == before and service.provider.seen == ["Ask"]
    assert any(message.startswith("Ask ") for message in reports) == (fault != "none")


def test_cli_host_query_dispatches_without_submitting(tmp_path, monkeypatch):
    from sico.cli import main

    calls = []
    monkeypatch.setattr("sico.service.host_process.run_host_submit",
                        lambda args: calls.append((args.command, args.launch_dir)) or 0)
    assert main(["host-query", "--launch-dir", str(tmp_path)]) == 0
    assert calls == [("host-query", str(tmp_path))]


def test_queue_full_receipt_keeps_original_id_and_stable_code(tmp_path, monkeypatch):
    from sico.service.service_business_dto import InputResult

    monkeypatch.setattr(FrontendAsk, "submit", lambda *_a, **_kw:
                        InputResult(None, "full", "rejected", "queue_full"))
    code, lines = run_worker(monkeypatch, tmp_path, target(tmp_path, "full"), sequence=20)
    assert code == 0 and lines == ["@rejected full queue_full"]


def test_query_before_submission_returns_unknown_without_starting_a_task(tmp_path, monkeypatch):
    context = target(tmp_path, "unsubmitted")
    with host_bridge(tmp_path, context) as (_, _client), business_service(
            tmp_path, monkeypatch, bridge_factory=BridgeClient) as service:
        code, lines = run_worker(monkeypatch, tmp_path, context, command="host-query")
        assert code == 0 and lines == ["@unknown unsubmitted"]
        assert not service.owner.controllers and not service.provider.seen


def test_failed_query_keeps_unknown_admission_slot(tmp_path, monkeypatch):
    def fail(*_args):
        raise ValueError("query unavailable")

    monkeypatch.setattr(FrontendAsk, "query", fail)
    code, lines = run_worker(monkeypatch, tmp_path, target(tmp_path, "unknown"),
                             command="host-query", sequence=1)
    assert code == 2 and lines == ["@unknown unknown"]
