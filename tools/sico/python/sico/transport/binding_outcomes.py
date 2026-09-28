"""Retained circuit outcome holds for one registered session."""

from ..core.contracts import NeedsReconcile
from .circuit import METHOD as CIRCUIT_METHOD
from .circuit import WRITES
from .methods import ASSISTANT_METHOD


def observe(record, context, circuit, observe_run):
    if record:
        state, run_ref = circuit.get("state"), circuit.get("run_ref")
        if isinstance(run_ref, str) and run_ref:
            if state in {"starting", "running", "stop_requested", "start_unknown", "context_lost"}:
                record.holds[run_ref] = state
            elif state in {"completed", "failed", "stopped"}:
                record.holds.pop(run_ref, None)
                observe_run(circuit)
        elif (state in {"outcome_unknown", "start_unknown", "context_lost"}
              and not any(row["source"] == context.record() for row in record.operations.values())):
            record.holds[context.target_id] = state


def unconfirmed(method, params, error):
    if method == ASSISTANT_METHOD or (
        method == CIRCUIT_METHOD and (params or {}).get("function") in
        WRITES | {"aiSimStop", "aiProjectClaim", "aiCopilotOpenTarget"}
    ):
        data = getattr(error, "data", {})
        return (data.get("operation_dispatched") is not False
                and (isinstance(error, NeedsReconcile) or error.code in {
                    "skill_timeout", "skill_response_pending", "router_persistence_failed"}))
    return False
