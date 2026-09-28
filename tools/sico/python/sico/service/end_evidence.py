"""Reconcile an original end intent without claiming process cleanup from journal drain."""

from ..transport.framing import ProtocolError
from .recovery_facts import read_facts
from .service_session_dto import SessionAddress


def end_state(project, address, operation_id):
    try:
        facts = read_facts(project, address.session.session_id,
                           end_operation=(address.session.runtime_id, operation_id))
    except FileNotFoundError:
        return "unknown"
    state = "unknown"
    for event in facts.ends:
        if event["kind"] not in {"session.end_requested", "session.end_observed"}:
            continue
        payload = event["payload"]
        if payload.get("operation_id") != operation_id:
            continue
        if SessionAddress.from_record(payload.get("address")) != address:
            raise ProtocolError("Session end intent belongs to another runtime")
        # A drained event precedes resource release; old-service cleanup is unconfirmed.
        state = "ended" if facts.resources_released else "needs_reconcile"
    return state
