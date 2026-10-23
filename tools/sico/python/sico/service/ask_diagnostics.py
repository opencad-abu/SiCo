"""Best-effort Ask queue metadata; durable route/inbox files remain authoritative."""

import json

from .diagnostics import diagnostics


def queue_event(status, input_id, sequence, *, session_id=None, pending=None, code=""):
    try:
        diagnostics.submit(__name__, json.dumps(dict(
            event="ask.queue", status=status, input_id=input_id, ask_sequence=sequence,
            session_id=session_id, pending=pending, code=code), sort_keys=True))
        if status == "rejected" and code == "queue_full":
            queue_event("queue_full", input_id, sequence, session_id=session_id,
                        pending=pending, code=code)
    except Exception:
        pass  # Logging cannot change admission or execution receipts.
