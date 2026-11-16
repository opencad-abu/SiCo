"""Binding reconciliation keeps a hold until evidence is terminal."""

from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sico.core.contracts import BoundContext, CircuitCallError, NeedsReconcile
from sico.transport import reconciliation
from sico.transport.reconciliation import BindingReconciliation, mutating


class Events:
    def __init__(self):
        self.rows = []
        self.fault = False

    def _publish(self, kind, row):
        self.rows.append((kind, row))


class Bindings:
    def __init__(self, record):
        self.record = record

    def require(self, _session_id, _context):
        return self.record


class Registry:
    def __init__(self, router=None):
        self.router = router

    def get(self, _instance, _generation):
        return self.router


class Broker:
    def __init__(self, context):
        self.bridge_id = "bridge"
        self._changed = nullcontext()
        self.binding_events = Events()
        self.record = SimpleNamespace(
            binding_id="binding",
            holds={},
            operations={},
            targets={context.target_id: context},
            invalidated=set(),
            releasing=False,
            anchor=context,
            revision=1,
        )
        self.bindings = Bindings(self.record)
        self._sessions = {"session": self.record}
        self._session_requests = {}
        self.registry = Registry()
        self.binding_events.fault = False


def context():
    return BoundContext("instance", "generation", "bound",
                        {"cellview": {"lib": "Lib", "cell": "Cell", "view_name": "schematic"}})


def params():
    return {"function": "aiCdfUpdateApply",
            "claim": {"claim": 1}, "expected": {"expected": 2},
            "values": [1, 2]}


def test_mutating_identifies_only_write_or_explicit_control_calls():
    assert mutating("assistant_call", {}) is True
    assert mutating("circuit_call", {"function": "aiCdfUpdateApply"}) is True
    assert mutating("circuit_call", {"function": "aiSimStop"}) is True
    assert mutating("circuit_call", {"function": "read_value"}) is False
    assert mutating("read", {"function": "aiSimStop"}) is False


def test_begin_records_hold_evidence_and_display():
    broker = Broker(context())
    value = BindingReconciliation(broker)
    value.begin("session", context(), "circuit_call", params(), "a" * 32)
    attempt = value.attempts["a" * 32]
    assert attempt["state"] == "pending" and attempt["settled"] is False
    assert broker.record.holds["a" * 32] == "pending"
    assert broker.binding_events.rows[0][0] == "binding.operation_started"
    assert broker.record.operations["a" * 32]["method"] == "circuit_call"
    attempt["result"] = {"state": "completed", "evidence": {"secret": 1},
                         "other": 2}
    value._display(attempt)
    assert broker.record.operations["a" * 32]["result"] == {
        "state": "completed", "other": 2}


def test_begin_rejects_attempt_overflow():
    broker = Broker(context())
    value = BindingReconciliation(broker)
    value.attempts = {str(index): {} for index in range(4096)}
    with pytest.raises(CircuitCallError) as caught:
        value.begin("session", context(), "read", {}, "a" * 32)
    assert caught.value.code == "binding_resource_limit"


@pytest.mark.parametrize("result,error,expected", [
    (None, CircuitCallError("x", "x", data={"operation_dispatched": False}),
     "not_dispatched"),
    (None, CircuitCallError("x", "x", data={}), "unknown"),
    ({"circuit": {"state": "outcome_unknown"}}, None, "unknown"),
    ({"circuit": {"state": "running", "run_ref": "run"}}, None, "running"),
    ({"circuit": {"state": "failed", "ok": False, "run_ref": "run"}}, None,
     "partial_or_failed"),
    ({"circuit": {"state": "completed", "ok": True, "run_ref": "run"}}, None,
     "completed"),
])
def test_finish_translates_result_and_error_states(result, error, expected):
    broker = Broker(context())
    value = BindingReconciliation(broker)
    value.begin("session", context(), "circuit_call", params(), "a" * 32)
    value.finish("a" * 32, result=result, error=error)
    attempt = value.attempts["a" * 32]
    assert attempt["state"] == expected
    if expected in reconciliation.TERMINAL:
        assert "a" * 32 not in broker.record.holds
    else:
        assert broker.record.holds["a" * 32] == expected


def test_finish_ignores_retired_binding_and_unknown_attempt():
    broker = Broker(context())
    value = BindingReconciliation(broker)
    value.attempts["a" * 32] = {"session_id": "missing", "binding_id": "binding"}
    value.finish("a" * 32)
    value.attempts["a" * 32] = {"session_id": "session", "binding_id": "old"}
    value.finish("a" * 32)
    with pytest.raises(KeyError):
        value.finish("missing")


def test_reject_admission_releases_hold_and_surfaces_fault():
    broker = Broker(context())
    value = BindingReconciliation(broker)
    value.begin("session", context(), "read", {}, "a" * 32)
    error = CircuitCallError("bad", "bad", data={"category": "validation"})
    value.reject_admission("a" * 32, error)
    assert "a" * 32 not in broker.record.holds
    assert broker.binding_events.rows[-1][0] == "binding.operation_rejected"
    broker.binding_events.fault = True
    value.begin("session", context(), "read", {}, "b" * 32)
    value.reject_admission("b" * 32, error)
    assert "b" * 32 in broker.record.holds


def test_observe_run_only_settles_the_matching_attempt():
    broker = Broker(context())
    value = BindingReconciliation(broker)
    value.begin("session", context(), "circuit_call",
                {"function": "aiSimRun", "values": ["setup"]}, "a" * 32)
    value.attempts["a" * 32]["run_ref"] = "run"
    value.observe_run("session", {"state": "completed", "run_ref": "run",
                                  "setup_ref": "setup"})
    assert value.attempts["a" * 32]["state"] == "completed"
    assert "a" * 32 not in broker.record.holds
    value.observe_run("missing", {"state": "completed"})
    value.observe_run("session", {"state": "running"})
    value.observe_run("session", {"state": "completed", "run_ref": "other",
                                  "setup_ref": "setup"})


def test_owner_rejects_retired_binding_and_target_identity():
    broker = Broker(context())
    value = BindingReconciliation(broker)
    value.begin("session", context(), "read", {}, "a" * 32)
    assert value._owner("session", "binding", "a" * 32)[0] is broker.record

    broker.record.binding_id = "other"
    with pytest.raises(NeedsReconcile):
        value._owner("session", "binding", "a" * 32)
    broker.record.binding_id = "binding"
    broker.record.releasing = True
    with pytest.raises(NeedsReconcile):
        value._owner("session", "binding", "a" * 32)
    broker.record.releasing = False
    with pytest.raises(CircuitCallError):
        value._owner("session", "binding", "missing")
    broker.record.targets.clear()
    with pytest.raises(NeedsReconcile):
        value._owner("session", "binding", "a" * 32)


def test_reconcile_uses_authenticated_router_evidence(monkeypatch):
    broker = Broker(context())
    value = BindingReconciliation(broker)
    value.begin("session", context(), "circuit_call", params(), "a" * 32)
    row = {
        "instance_id": "instance", "generation": "generation",
        "binding_id": "binding", "router_id": "router", "bridge_id": "bridge",
    }
    router = SimpleNamespace(
        router_id="router",
        journal=SimpleNamespace(receipt=lambda *_args, **_kwargs: row),
        snapshot=lambda: {"closed": False, "unknown": None, "active": False},
    )
    broker.registry.router = router
    value.attempts["a" * 32]["settled"] = True
    monkeypatch.setattr(value, "_evidence", lambda *_args: "completed")
    monkeypatch.setattr(value, "_apply",
                        lambda session, binding, request, original, router, state, evidence:
                        state)
    assert value.reconcile("session", "binding", "a" * 32) == "completed"

    router.journal.receipt = lambda *_args, **_kwargs: dict(row, bridge_id="other")
    assert value.reconcile("session", "binding", "a" * 32) == "evidence_conflict"
    router.journal.receipt = lambda *_args, **_kwargs: row
    value.attempts["a" * 32]["settled"] = False
    assert value.reconcile("session", "binding", "a" * 32) == "pending"


def test_reconcile_marks_missing_or_unknown_router_evidence():
    broker = Broker(context())
    value = BindingReconciliation(broker)
    value.begin("session", context(), "read", {}, "a" * 32)
    captured = []
    value._apply = lambda *args: captured.append(args) or args[-2]
    assert value.reconcile("session", "binding", "a" * 32) == "unknown"
    assert "error" in captured[-1][-1]


def evidence_attempt(request_id="a" * 32):
    return {
        "request_id": request_id,
        "session_id": "session",
        "binding_id": "binding",
        "function": "aiCdfUpdateApply",
        "claim": ["session", "cdf-update:" + request_id, "digest"],
        "expected": ["instance", "generation", "bound"],
        "values": [],
        "run_ref": None,
        "settled": True,
    }


def test_evidence_rejects_identity_mismatch_and_undispatched_reply():
    value = BindingReconciliation(Broker(context()))
    request_id = "a" * 32
    attempt = evidence_attempt(request_id)
    assert value._evidence(
        attempt,
        {"state": "completed", "reply": {
            "id": request_id, "ok": True,
            "result": {"circuit": {
                "ok": True, "saved": True, "target": "bound",
                "update_ref": "cdf-update:" + request_id}}}},
        object(), {}) == "completed"
    assert value._evidence(
        attempt, {"reply": {"id": "b" * 32}}, object(), {}) == "evidence_conflict"
    assert value._evidence(
        attempt,
        {"reply": {"id": request_id, "ok": False,
                   "diagnostic": {"operation_dispatched": False}}},
        object(), {}) == "not_dispatched"
    assert value._evidence(
        attempt, {"state": "queue_timeout"}, object(), {}) == "not_dispatched"
    attempt["function"] = "unknown"
    assert value._evidence(attempt, {}, object(), {}) == "unknown"


def test_evidence_rejects_missing_claim_prefix_and_wrong_target():
    value = BindingReconciliation(Broker(context()))
    request_id = "a" * 32
    attempt = evidence_attempt(request_id)
    attempt["claim"] = None
    assert value._evidence(attempt, {}, object(), {}) == "unknown"
    attempt = evidence_attempt(request_id)
    attempt["claim"][1] = "other:" + request_id
    assert value._evidence(attempt, {}, object(), {}) == "evidence_conflict"
    attempt = evidence_attempt(request_id)
    reply = {"id": request_id, "ok": True, "result": {"circuit": {
        "ok": True, "saved": True, "target": "other",
        "update_ref": "cdf-update:" + request_id}}}
    assert value._evidence(attempt, {"reply": reply}, object(), {}) == "evidence_conflict"


def test_apply_is_idempotent_and_clears_terminal_holds():
    broker = Broker(context())
    value = BindingReconciliation(broker)
    value.begin("session", context(), "circuit_call", params(), "a" * 32)
    original = deepcopy(value.attempts["a" * 32])
    saved = {"request_id": "a" * 32}
    router = SimpleNamespace(
        journal=SimpleNamespace(lookup=lambda _request: saved),
        session_unresolved=lambda _session: False,
    )
    broker.registry.router = router
    result = value._apply("session", "binding", "a" * 32, original, router,
                          "completed", {"router_receipt": saved})
    assert result["state"] == "completed"
    assert "a" * 32 not in broker.record.holds
    again = value._apply("session", "binding", "a" * 32, original, router,
                         "completed", {"router_receipt": saved})
    assert again == result


def test_apply_downgrades_changed_or_conflicting_evidence():
    broker = Broker(context())
    value = BindingReconciliation(broker)
    value.begin("session", context(), "circuit_call", params(), "a" * 32)
    original = deepcopy(value.attempts["a" * 32])
    router = SimpleNamespace(
        journal=SimpleNamespace(lookup=lambda _request: {"other": 1}),
        session_unresolved=lambda _session: False,
    )
    broker.registry.router = router
    assert value._apply("session", "binding", "a" * 32, original, router,
                        "completed", {"router_receipt": {"request_id": "a" * 32}})[
        "state"] == "pending"
    broker.registry.router = None
    assert value._apply("session", "binding", "a" * 32, original, router,
                        "completed", {})["state"] == "evidence_conflict"
    broker.registry.router = router
    broker._session_requests["session"] = {"request": 1}
    assert value._apply("session", "binding", "a" * 32, original, router,
                        "completed", {})["state"] == "pending"
