"""Exact-request recovery. Evidence can clear execution holds, never ownership."""

import json
import time
import uuid
from copy import deepcopy

from cadai.project_contract import receipt as checked_receipt
from cadai.project_schema import OPERATIONS

from ..core.contracts import BoundContext, CircuitCallError, NeedsReconcile, json_copy
from .bindings import binding_error
from .circuit import WRITES
from .methods import QueryUnavailable
from .router_journal import RouterJournal, record_digest

FUNCTIONS = dict(
    zip(
        (
            "aiCdfUpdateApply",
            "aiLibraryCreate",
            "aiCreateExecute",
            "aiTemplateSymbolCreate",
            "aiDrawSymbol",
            "aiConfigCreate",
            "aiSimCreate",
            "aiSimRun",
        ),
        (
            "apply_cdf_update",
            "create_circuit_library",
            "create_circuit_from_plan",
            "create_template_symbol",
            "draw_symbol",
            "create_circuit_config",
            "create_simulation_setup",
            "run_simulation_setup",
        ),
    )
)
TERMINAL = {"completed", "partial_or_failed", "not_dispatched"}
UNCERTAIN = {"outcome_unknown", "start_unknown", "context_lost"}


def mutating(method, params):
    return method == "assistant_call" or (
        method == "circuit_call"
        and params.get("function")
        in WRITES | {"aiSimStop", "aiProjectClaim", "aiCopilotOpenTarget"}
    )


class BindingReconciliation:
    def __init__(self, broker):
        self.broker = broker
        self.attempts = {}

    def begin(self, session_id, context, method, params, request_id):
        """Caller holds broker lock; persistence failure prevents native dispatch."""
        record = self.broker.bindings.require(session_id, context)
        if len(self.attempts) >= 4096:
            raise binding_error(
                "binding_resource_limit", "Operation evidence limit reached", context
            )
        attempt = dict(
            request_id=request_id,
            session_id=session_id,
            binding_id=record.binding_id,
            instance_id=context.instance_id,
            generation=context.generation,
            source=context.record(),
            method=method,
            function=(params or {}).get("function"),
            claim=json_copy((params or {}).get("claim")),
            expected=json_copy((params or {}).get("expected")),
            values=json_copy((params or {}).get("values", [])) if method == "circuit_call" else [],
            state="pending",
            settled=False,
            run_ref=None,
        )
        self.broker.binding_events._publish(
            "binding.operation_started", dict(event_id=uuid.uuid4().hex, actor="host", **attempt)
        )
        self.attempts[request_id] = attempt
        # The hold is present before native entry, including a lost caller/response.
        record.holds[request_id] = "pending"
        self._display(attempt)

    def _display(self, attempt):
        record = self.broker._sessions[attempt["session_id"]]
        record.operations[attempt["request_id"]] = {
            key: json_copy(attempt.get(key))
            for key in (
                "request_id",
                "binding_id",
                "function",
                "method",
                "source",
                "state",
                "run_ref",
            )
        }
        if attempt.get("result"):
            record.operations[attempt["request_id"]]["result"] = {
                key: value for key, value in attempt["result"].items() if key != "evidence"
            }

    def finish(self, request_id, result=None, error=None):
        with self.broker._changed:
            attempt = self.attempts[request_id]
            record = self.broker._sessions.get(attempt["session_id"])
            if not record or record.binding_id != attempt["binding_id"]:
                return
            attempt["settled"] = True
            circuit = (result or {}).get("circuit", {})
            diagnostic = getattr(error, "data", {})
            if error:
                state = (
                    "not_dispatched"
                    if diagnostic.get("operation_dispatched") is False
                    else "unknown"
                )
            elif circuit.get("state") in UNCERTAIN:
                state = "unknown"
            elif circuit.get("run_ref") and circuit.get("state") in {
                "starting",
                "running",
                "stop_requested",
            }:
                state = "running"
            else:
                state = "partial_or_failed" if circuit.get("ok") is False else "completed"
            attempt["state"] = state
            attempt["run_ref"] = circuit.get("run_ref")
            if state in TERMINAL:
                record.holds.pop(request_id, None)
            else:
                record.holds[request_id] = state
            self._display(attempt)

    def reject_admission(self, request_id, error):
        """Broker lock is already held; no native invocation occurred."""
        attempt = self.attempts[request_id]
        attempt.update(settled=True, state="not_dispatched")
        record = self.broker._sessions[attempt["session_id"]]
        if not self.broker.binding_events.fault:
            self.broker.binding_events._publish("binding.operation_rejected", dict(
                event_id=uuid.uuid4().hex, session_id=attempt["session_id"],
                binding_id=attempt["binding_id"], request_id=request_id,
                instance_id=attempt["instance_id"], generation=attempt["generation"],
                source=json_copy(attempt["source"]),
                actor="host", operation_dispatched=False, message=str(error)))
            record.holds.pop(request_id, None)
        self._display(attempt)

    def observe_run(self, session_id, circuit):
        """An authenticated status for another run cannot clear this attempt."""
        if circuit.get("state") not in {"completed", "failed", "stopped"}:
            return
        record = self.broker._sessions.get(session_id)
        if not record:
            return
        for attempt in self.attempts.values():
            if (
                attempt["session_id"] == session_id
                and attempt["binding_id"] == record.binding_id
                and attempt.get("run_ref") == circuit.get("run_ref")
                and attempt["function"] == "aiSimRun"
                and attempt["values"][0] == circuit.get("setup_ref")
            ):
                attempt["state"] = (
                    "completed" if circuit["state"] == "completed" else "partial_or_failed"
                )
                record.holds.pop(attempt["request_id"], None)
                self._display(attempt)

    def _owner(self, session_id, binding_id, request_id):
        record = self.broker._sessions.get(session_id)
        if not record or record.releasing or record.binding_id != binding_id:
            raise NeedsReconcile("Reconciliation belongs to a retired binding lifetime")
        attempt = self.attempts.get(request_id)
        if (
            not attempt
            or attempt["session_id"] != session_id
            or attempt["binding_id"] != binding_id
        ):
            raise binding_error(
                "binding_target_not_owned", "Original operation is not owned", record.anchor
            )
        source = BoundContext.from_record(attempt["source"])
        if record.targets.get(source.target_id) != source:
            raise NeedsReconcile("Original operation target identity changed")
        return record, attempt, source

    def reconcile(self, session_id, binding_id, request_id):
        RouterJournal.request_id(request_id)
        with self.broker._changed:
            record, attempt, source = self._owner(session_id, binding_id, request_id)
            if attempt.get("result", {}).get("state") in TERMINAL:
                return json_copy(attempt["result"])
            original = deepcopy(attempt)
            router = self.broker.registry.get(source.instance_id, source.generation)
            candidates = [
                value for key, value in record.targets.items() if key not in record.invalidated
            ]
            candidates.sort(key=lambda value: bool(value.snapshot.get("cellview")))
            probe = candidates[0] if candidates else None
        # Never wait for the router or native I/O while holding the broker lock.
        evidence = {}
        state = "unknown"
        try:
            if router is None or router.journal is None:
                raise NeedsReconcile("Original router evidence unavailable")
            row = router.journal.receipt(
                request_id, session_id=session_id, target_id=source.target_id
            )
            evidence["router_receipt"] = row
            expected = dict(
                instance_id=source.instance_id,
                generation=source.generation,
                binding_id=binding_id,
                router_id=router.router_id,
                bridge_id=self.broker.bridge_id,
            )
            if any(row.get(key) != value for key, value in expected.items()):
                state = "evidence_conflict"
            elif not original["settled"]:
                state = "pending"
            else:
                snapshot = router.snapshot()
                if snapshot["closed"] or snapshot["unknown"] or snapshot["active"]:
                    state = "unknown" if snapshot["unknown"] or snapshot["closed"] else "pending"
                else:
                    state = self._evidence(original, row, probe, evidence)
        except (NeedsReconcile, QueryUnavailable, OSError) as error:
            evidence["error"] = str(error)
        except (ValueError, TypeError, KeyError, IndexError, CircuitCallError) as error:
            evidence["error"] = str(error)
            state = "evidence_conflict"
        return self._apply(session_id, binding_id, request_id, original, router, state, evidence)

    def _evidence(self, attempt, row, probe, evidence):
        reply = row.get("reply") or {}
        if reply and reply.get("id") != attempt["request_id"]:
            return "evidence_conflict"
        diagnostic = reply.get("diagnostic") or {}
        if reply.get("ok") is False and diagnostic.get("operation_dispatched") is False:
            return "not_dispatched"
        if row.get("state") in {"cancelled_before_start", "queue_timeout", "queue_full"}:
            return "not_dispatched"
        if attempt["function"] not in FUNCTIONS:
            return "unknown"
        operation = FUNCTIONS[attempt["function"]]
        claim, expected = attempt["claim"], attempt["expected"]
        if not claim or not expected or not probe:
            return "unknown"
        prefix = OPERATIONS[operation]
        if not claim[1].startswith(prefix):
            return "evidence_conflict"
        record = dict(operation=operation, request_id=claim[1][len(prefix) :], input_summary={})
        if operation == "run_simulation_setup":
            record["input_summary"]["setup_ref"] = attempt["values"][0]
        native = (reply.get("result") or {}).get("circuit")
        # An incomplete/error response needs an exact read-only native receipt query.
        if (
            reply.get("ok") is not True
            or not isinstance(native, dict)
            or operation == "run_simulation_setup"
        ):
            from .broker import skill_call_context

            with skill_call_context(session_id=attempt["session_id"]):
                observed = self.broker.read(
                    probe,
                    "project_receipt",
                    dict(
                        session_id=claim[0],
                        key=claim[1],
                        input_digest=claim[2],
                        operation=operation,
                    ),
                )
            evidence["native_observation"] = observed
            project = observed["project"]
            raw = project.get("receipt_json")
            if raw is None or project.get("ok") is not True:
                return "unknown"
            native = json.loads(raw)
            if (
                operation == "run_simulation_setup"
                and project.get("run_context_checked") is not True
            ):
                return "unknown"
        checked_receipt(native, record)
        evidence["business_receipt"] = native
        if native.get("state") in UNCERTAIN:
            return "unknown"
        if operation == "run_simulation_setup":
            if attempt.get("run_ref") and native["run_ref"] != attempt["run_ref"]:
                return "evidence_conflict"
            if native["state"] in {"starting", "running", "stop_requested", "preflight"}:
                return "running"
            if native["state"] not in {"completed", "failed", "stopped"}:
                return "unknown"
            return "completed" if native["state"] == "completed" else "partial_or_failed"
        if operation == "create_circuit_library":
            if native.get("library") != expected[2][0] or native.get("library_path") != expected[5]:
                return "evidence_conflict"
        elif native.get("target") != expected[2]:
            return "evidence_conflict"
        return "completed" if native["ok"] and native["saved"] else "partial_or_failed"

    def _apply(self, session_id, binding_id, request_id, original, router, state, evidence):
        with self.broker._changed:
            record, attempt, source = self._owner(session_id, binding_id, request_id)
            if attempt.get("result", {}).get("state") in TERMINAL:
                return json_copy(attempt["result"])
            if attempt != original:
                state = "pending"
            if self.broker.registry.get(source.instance_id, source.generation) is not router:
                state = "evidence_conflict"
            saved = evidence.get("router_receipt")
            if (
                state not in {"evidence_conflict", "not_dispatched"}
                and saved
                and router
                and router.journal.lookup(request_id) != saved
            ):
                state = "pending"
            if state in TERMINAL and (
                self.broker._session_requests.get(session_id)
                or router is None
                or router.session_unresolved(session_id)
            ):
                state = "pending"
            digest = record_digest(dict(state=state, evidence=evidence))
            if attempt.get("result", {}).get("evidence_digest") == digest:
                return json_copy(attempt["result"])
            result = dict(
                contract="cad_ai_binding_reconciliation.v1",
                event_id=uuid.uuid4().hex,
                actor="host",
                session_id=session_id,
                binding_id=binding_id,
                request_id=request_id,
                instance_id=source.instance_id,
                generation=source.generation,
                source=source.record(),
                state=state,
                hold_cleared=state in TERMINAL,
                ownership_released=False,
                automatic_resume_allowed=False,
                evidence=evidence,
                evidence_digest=digest,
                checked_at=time.time(),
            )
            self.broker.binding_events._publish("binding.operation_reconciled", result)
            attempt.update(state=state, result=json_copy(result))
            native = evidence.get("business_receipt", {})
            if native.get("run_ref"):
                attempt["run_ref"] = native["run_ref"]
            if state in TERMINAL:
                record.holds.pop(request_id, None)
                if attempt.get("run_ref"):
                    record.holds.pop(attempt["run_ref"], None)
            else:
                record.holds[request_id] = state
            self._display(attempt)
            return json_copy(result)
