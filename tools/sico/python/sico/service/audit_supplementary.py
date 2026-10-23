"""Release supplementary native questions so the model may decide."""

import uuid

from ..core.review_classes import SUPPLEMENTARY


def answer(audit):
    """Send the empty answer map used by Codex for a non-blocking request."""
    pending = audit.pending
    candidates = [
        key for key, value in pending.items()
        if value.get("source") == "native"
        and value.get("rpc_id") is not None
        and value.get("review_class") == SUPPLEMENTARY
        and not value.get("blocking", True)
    ]
    if not candidates:
        return 0
    with audit.runtime.lock:
        audit.index.sync()
        selected = [
            key for key in candidates
            if audit.index.audits.get(key, {}).get("status") == "pending"
            and not audit.index.audits.get(key, {}).get("reply")
        ]
        for key in selected:
            audit.emit("reply_received", {
                "id": key, "reply_id": "auto_" + uuid.uuid4().hex,
                "answers": {}, "actor": "SiCo", "automatic": True,
            })
    return audit.deliver()
