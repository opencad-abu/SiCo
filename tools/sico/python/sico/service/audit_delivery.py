"""Validate and deliver native audit replies exactly once on the worker."""

from copy import deepcopy

from ..codex.approval_requests import response as approval_response
from ..codex.rpc import RpcError
from ..core.contracts import NeedsReconcile


def deliver(self):
    """Called by the RPC consumer, never by Qt or the MCP server thread."""
    delivered = self.elicitations.deliver()
    poll = getattr(getattr(self.loop.runtime, "rpc", None), "poll_notifications", None)
    if self.pending and callable(poll):
        poll()
    for key, pending in list(self.pending.items()):
        if pending.get("source") == "elicitation":
            continue
        with self.loop.lock:
            self.index.sync()
            row = deepcopy(self.index.audits[key])
            if row["status"] != "answer_received" or pending["rpc_id"] is None:
                continue
            if self.loop.cancelled() or self.loop.stale:
                return
        try:
            self.native_current(key)
        except ValueError:
            self.end_inputs("原交互已结束或来源变化，答复未交付", keys=[key])
            continue
        self.loop._activity("validating_audit", "核验答复依据与目标")
        try:
            self.index.verify_evidence(row["evidence"])
            if row["context"] != self.loop.state.context.record():
                raise ValueError("Captured target changed")
            self.loop.validate_audit_target()
            self.index.verify_evidence(row["evidence"])
        except (ValueError, OSError, TypeError, KeyError, NeedsReconcile):
            with self.loop.lock:
                self.emit("invalidated", {
                    "id": key, "reason": "目标或证据不可用，答复未交付；请核对后重新发起任务。",
                })
                self.loop.stale = True
                self.loop.stale_reason = "Audit target or evidence invalid; no continuation"
            return
        with self.loop.lock:
            if self.loop.cancelled() or self.loop.stale:
                return
            response = approval_response(row, pending)
            try:
                # Persist before the only permitted send. A late resolution wins.
                try:
                    self.native_current(key)
                except ValueError:
                    self.end_inputs("交付前原交互已结束；答复未发送", keys=[key])
                    continue
                self.emit("dispatching", {"id": key, "reply_id": row["reply"]["reply_id"]})
                try:
                    self.native_current(key)
                except ValueError:
                    self.end_inputs("交付前原交互已结束；答复未发送", keys=[key])
                    continue
            finally:
                self.pending.pop(key, None)
                self.loop.state.task["waiting_audits"] = list(self.pending)
            try:
                self.requests.connection.send({"id": pending["rpc_id"],
                                               "result": response})
            except (OSError, ValueError, RuntimeError):
                self.emit("unconfirmed", {"id": key, "reason": "答复发送未确认；不会重发"})
                raise RpcError("Native input delivery is unconfirmed; no replay") from None
            self.emit("resumed", {"id": key, "reply_id": row["reply"]["reply_id"]})
            self.loop._activity("waiting_model", "等待 LLM 响应")
            delivered += 1
    return delivered

def native_current(self, key):
    pending = self.pending.get(key)
    row = self.index.audits[key]
    if (not pending or not self.loop.active or self.loop.cancelled() or self.loop.stale
            or row["task_id"] != self.loop.state.task.get("id")
            or row["context"] != self.loop.state.context.record()
            or not self.loop.runtime or self.loop.runtime.rpc is not self.requests.connection
            or pending["binding"].get("connection_id") != self.loop.connection_id):
        raise ValueError("Native input is no longer current")
    if pending.get("child_thread_id"):
        self.loop.child_inputs.current(pending["child_thread_id"],
                                       turn_id=pending["child_turn_id"])
    elif pending.get("source") == "approval" and (
            pending["binding"]["threadId"] != self.loop.thread_id
            or pending["binding"]["turnId"] != self.loop.steering.turn_id):
        raise ValueError("Approval turn is no longer current")
