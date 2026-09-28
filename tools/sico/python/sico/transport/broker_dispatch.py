"""One admitted native dispatch and its durable completion receipt."""

from ..core.contracts import NeedsReconcile
from .framing import ProtocolError
from .methods import QueryUnavailable


def dispatch(context, method, params, request_id, resources, *, call_context,
             admission_lock, peers, binding_live, reconciliation, native_bindings, discard_peer):
    from .reconciliation import mutating

    # A prerequisite may have appeared while this request waited in the
    # instance FIFO. Recheck before reservations or any native entry.
    before_start = call_context.get("before_start")
    blocked = before_start() if before_start is not None else None
    if blocked is not None:
        raise QueryUnavailable(blocked.status, blocked.summary)
    session_id = call_context.get("session_id")
    with admission_lock:
        peer = peers.get(context.instance_id)
        if peer is None or peer.generation != context.generation:
            raise NeedsReconcile("Virtuoso generation retired before dispatch")
        identity = None
        tracked = bool(session_id and mutating(method, params or {}))
        if session_id and not binding_live(context):
            from .bindings import binding_error

            raise binding_error(
                "binding_target_closed", "Captured window closed or changed", context)
        if tracked:
            # Durable attempt evidence must exist before either resource
            # reservation or native entry; a journal failure therefore
            # cannot leave an untracked write in flight.
            reconciliation.begin(session_id, context, method, params, request_id)
        if session_id:
            try:
                identity = native_bindings.dispatch(session_id, context, method, params,
                    request_id, call_context.get("task_id", ""), resources)
            except Exception as error:
                if tracked:
                    # No peer call has happened. Do not recurse into the
                    # non-reentrant broker lock while recording rejection.
                    reconciliation.reject_admission(request_id, error)
                raise
    try:
        try:
            result = peer.call(context, method, params, request_id, native_identity=identity)
        except Exception as error:
            if tracked:
                reconciliation.finish(request_id, error=error)
            raise
        if tracked:
            reconciliation.finish(request_id, result=result)
        return result
    except (OSError, EOFError, ProtocolError):
        discard_peer(peer)
        raise
