"""Host operation receipts keep their identity across Bridge, journal and UI."""

from copy import deepcopy

import pytest

from sico.core.contracts import BoundContext
from sico.core.session_events import EventCursor, SESSION_CONTRACT
from sico.transport.broker import ContextBroker


CONTEXT = BoundContext("instance", "generation", "ciw", {})


def batch(payload, kind="binding.operation_started", sequence=1):
    return dict(
        contract=SESSION_CONTRACT, session_id="session", runtime_id="runtime",
        instance_id=CONTEXT.instance_id, generation=CONTEXT.generation,
        events=[dict(session_id="session", sequence=sequence, task_id="", kind=kind,
                     payload=payload)],
    )


@pytest.mark.parametrize("kind", ["binding.operation_started", "binding.operation_reconciled",
                                 "binding.operation_rejected"])
@pytest.mark.parametrize("legacy", [False, True])
def test_operation_source_identity_is_accepted_without_an_active_task(kind, legacy):
    payload = dict(session_id="session", source=CONTEXT.record())
    if not legacy:
        payload.update(instance_id=CONTEXT.instance_id, generation=CONTEXT.generation)
    cursor = EventCursor("session", "runtime", CONTEXT)
    assert cursor.consume(batch(payload, kind))
    assert cursor.sequence == 1 and cursor.task_id == ""


@pytest.mark.parametrize("change", ["session", "instance", "generation", "conflict",
                                   "partial", "missing", "malformed", "contract"])
def test_foreign_or_missing_operation_identity_is_rejected_atomically(change):
    payload = dict(session_id="session", source=CONTEXT.record())
    if change == "session":
        payload["session_id"] = "foreign"
    elif change in {"instance", "generation"}:
        payload["source"]["instance_id" if change == "instance" else "generation"] = "foreign"
    elif change == "conflict":
        payload.update(instance_id="foreign", generation=CONTEXT.generation)
    elif change == "partial":
        payload["instance_id"] = CONTEXT.instance_id
    elif change == "missing":
        payload.pop("source")
    elif change == "malformed":
        payload["source"] = None
    elif change == "contract":
        payload["source"]["contract"] = "foreign"
    cursor = EventCursor("session", "runtime", CONTEXT)
    with pytest.raises(ValueError):
        cursor.consume(batch(payload))
    assert cursor.sequence == 0


def test_historical_operation_source_does_not_retarget_live_session():
    cursor = EventCursor("session", "runtime", CONTEXT, history_sequence=1)
    historical = BoundContext("old-instance", "old-generation", "old-target", {})
    payload = dict(session_id="session", source=historical.record())
    assert cursor.consume(batch(payload))
    with pytest.raises(ValueError, match="another Virtuoso"):
        cursor.consume(batch(payload, sequence=2))
    assert cursor.sequence == 1


def test_started_and_rejected_receipts_publish_complete_source_identity():
    with ContextBroker() as broker:
        broker.bindings.add("session", CONTEXT)
        with broker._changed:
            broker.reconciliation.begin("session", CONTEXT, "assistant_call", {}, "a" * 32)
            broker.reconciliation.reject_admission("a" * 32, ValueError("admission refused"))
            events = deepcopy(broker.binding_events.events)
    cursor = EventCursor("session", "runtime", CONTEXT)
    assert [event["kind"] for event in events] == [
        "binding.operation_started", "binding.operation_rejected",
    ]
    for event in events:
        payload = event["payload"]
        assert (payload["instance_id"], payload["generation"]) == ("instance", "generation")
        assert payload["source"] == CONTEXT.record()
        cursor.consume(batch(payload, event["kind"], event["sequence"]))


def test_reconciliation_receipt_publishes_the_same_source_identity():
    with ContextBroker() as broker:
        record = broker.bindings.add("session", CONTEXT)
        with broker._changed:
            broker.reconciliation.begin("session", CONTEXT, "assistant_call", {}, "a" * 32)
        # A missing router leaves the result unknown, but still emits a receipt
        # the desktop must be able to display without ending the session.
        result = broker.reconciliation.reconcile("session", record.binding_id, "a" * 32)
        events = deepcopy(broker.binding_events.events)
        assert result["state"] == "unknown" and not result["hold_cleared"]
    assert events[-1]["kind"] == "binding.operation_reconciled"
    assert result["source"] == CONTEXT.record()
    assert (result["instance_id"], result["generation"]) == ("instance", "generation")
    cursor = EventCursor("session", "runtime", CONTEXT)
    for event in events:
        cursor.consume(batch(event["payload"], event["kind"], event["sequence"]))
