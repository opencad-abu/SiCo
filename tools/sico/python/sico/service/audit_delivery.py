"""Validate and deliver native audit replies exactly once on the worker."""

from copy import deepcopy

from ..codex.approval_requests import response as approval_response
from ..codex.input_delivery import InputDelivery, send_controls
from ..core.target_validation import TargetUnconfirmed
from ..core.contracts import NeedsReconcile


def deliver(self):
    """Called by the RPC consumer, never by Qt or the MCP server thread."""
    delivered = self.elicitations.deliver()
    poll = getattr(self.requests.connection, "poll_notifications", None)
    if self.pending and callable(poll):
        poll()
    for key, pending in list(self.pending.items()):
        if pending.get("source") == "elicitation":
            continue
        with self.runtime.lock:
            self.index.sync()
            row = deepcopy(self.index.audits[key])
            if row["status"] != "answer_received" or pending["rpc_id"] is None:
                continue
            if not self.runtime.admitted():
                return
        try:
            self.native_current(key)
        except ValueError:
            self.end_inputs("原交互已结束或来源变化，答复未交付", keys=[key])
            continue
        self.runtime.activity("validating_audit", "核验答复依据与目标")
        if not row["reply"].get("automatic"):
            try:
                self.index.verify_evidence(row["evidence"])
                if row["context"] != self.runtime.snapshot().context:
                    raise ValueError("Captured target changed")
                self.runtime.validate_target()
                self.index.verify_evidence(row["evidence"])
            except TargetUnconfirmed as exc:
                self.runtime.activity("waiting_user", str(exc))
                continue
            except (ValueError, OSError, TypeError, KeyError, NeedsReconcile):
                with self.runtime.lock:
                    self.requests.cancel(key, lambda: self.emit("invalidated", {
                        "id": key, "reason": "目标或证据不可用，答复未交付；请核对后重新发起任务。",
                    }))
                    self.runtime.invalidate("Audit target or evidence invalid; no continuation")
                return
        with self.runtime.lock:
            delivery, replies = prepare(self, key, row, pending)
        send_controls(replies)
        if delivery:
            delivered += delivery.send()
    return delivered


def prepare(self, key, row, pending):
    """Called under the state lock; return a delivery claim or cancellation frames."""
    if not self.runtime.admitted():
        return None, []
    response = approval_response(row, pending)
    try:
        self.native_current(key)
    except ValueError:
        return None, self.withdraw_inputs("交付前原交互已结束；答复未发送", keys=[key])
    delivery = InputDelivery(
        key, pending, self.requests.connection, response, self.emit,
        runtime=self.runtime, requests=self.requests, row=row,
        status=lambda: self.index.audits[key]["status"])
    consumed = self.requests.resolve(key, lambda: self.emit(
        "dispatching", {"id": key, "reply_id": row["reply"]["reply_id"]}))
    if consumed is None:
        return None, []
    try:
        native_current(self, key, pending=consumed)
    except ValueError:
        self.emit("invalidated", {"id": key, "reason": "交付前原交互已结束；答复未发送"})
        return None, self.requests.cancellation(consumed)
    return delivery, []


def native_current(self, key, *, pending=None):
    pending = pending or self.pending.get(key)
    row, scope = self.index.audits[key], self.runtime.snapshot()
    if (not pending or not scope.admitted
            or row["task_id"] != scope.task_id or row["context"] != scope.context
            or not self.requests.is_current()
            or pending["binding"].get("connection_id") != self.requests.connection_id
            or self.requests.is_closed(pending["binding"].get("threadId"), pending["rpc_id"])):
        raise ValueError("Native input is no longer current")
    if pending.get("child_thread_id"):
        self.runtime.child_current(pending["child_thread_id"], turn_id=pending["child_turn_id"])
    elif pending.get("source") == "approval" and (
            pending["binding"]["threadId"] != scope.thread_id
            or pending["binding"]["turnId"] != scope.turn_id):
        raise ValueError("Approval turn is no longer current")
