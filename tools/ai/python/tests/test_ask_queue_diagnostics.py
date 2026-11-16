"""Queue diagnostics contain admission metadata and cannot change receipts."""

import json

from agent_service_business_fixtures import business_service
from sico.service.ask_diagnostics import queue_event
from sico.transport.bridge_client import BridgeClient
from test_agent_service_host import host_bridge, target
from test_ask_queue import ask


def test_queue_events_have_sequence_and_no_prompt_or_context(tmp_path, monkeypatch):
    rows = []
    monkeypatch.setattr("sico.service.ask_diagnostics.diagnostics.submit",
                        lambda name, message, *_args: rows.append((name, message)))
    with host_bridge(tmp_path, target(tmp_path)), business_service(
            tmp_path, monkeypatch, bridge_factory=BridgeClient) as service:
        receipt = ask(tmp_path, target(tmp_path, "first"), 1)
        owner, = service.owner.controllers.values()
        assert owner.wait(5)
        assert receipt.outcome == "accepted"
    events = [json.loads(message) for name, message in rows
              if name == "sico.service.ask_diagnostics"]
    assert [row["status"] for row in events] == ["received", "accepted", "queued", "started"]
    assert all(row["ask_sequence"] == 1 and row["input_id"] == "first" for row in events)
    assert all(set(row) == {"event", "status", "input_id", "ask_sequence",
                           "session_id", "pending", "code"} for row in events)
    rows.clear()
    queue_event("rejected", "full", 20, code="queue_full")
    assert [json.loads(message)["status"] for _name, message in rows] == ["rejected", "queue_full"]


def test_diagnostic_failure_does_not_change_durable_acceptance(tmp_path, monkeypatch):
    def fail(*_args):
        raise OSError("diagnostic channel unavailable")

    monkeypatch.setattr("sico.service.ask_diagnostics.diagnostics.submit", fail)
    with host_bridge(tmp_path, target(tmp_path)), business_service(
            tmp_path, monkeypatch, bridge_factory=BridgeClient) as service:
        receipt = ask(tmp_path, target(tmp_path, "first"), 1)
        owner, = service.owner.controllers.values()
        assert owner.wait(5)
        assert receipt.outcome == "accepted" and service.provider.seen == ["first"]
