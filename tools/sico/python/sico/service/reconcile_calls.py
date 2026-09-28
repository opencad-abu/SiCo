"""Reconstruct reconciliation objects from durable tool dispatch/receipt pairs."""

from copy import deepcopy
from dataclasses import dataclass, field


@dataclass
class ReconcileCalls:
    """Replayable projection; proposed but undispatched model calls never enter it."""

    task_id: str = ""
    calls: dict = field(default_factory=dict)
    decisions: dict = field(default_factory=dict)

    def receive(self, event):
        kind, payload = event["kind"], event["payload"]
        if kind == "task.started":
            self.task_id = event.get("task_id", "")
            self.calls.clear()
            self.decisions.clear()
        elif kind == "tool.started":
            self.task_id = event.get("task_id", self.task_id)
            self.calls[payload["id"]] = dict(deepcopy(payload),
                at=event.get("timestamp", ""), outcome="unconfirmed")
        elif kind == "session.tool_receipt" and payload.get("task_id") == self.task_id:
            row = self.calls.get(payload.get("id"))
            if row is not None:
                row["late_result"] = deepcopy(payload.get("result"))
        elif kind == "tool.finished":
            result = payload.get("result") or {}
            previous = self.calls.pop(payload.get("id"), None)
            if (result.get("status") == "needs_reconcile"
                    or "NeedsReconcile" in str(result.get("summary", ""))):
                row = previous or dict(id=payload["id"], name=payload["name"], input={})
                row.update(result=deepcopy(result), outcome="needs_reconcile")
                if payload.get("native"):
                    row["native"] = deepcopy(payload["native"])
                self.calls[payload["id"]] = row
        elif kind == "session.reconcile_decided" and event.get("task_id") == self.task_id:
            self.decisions[payload["call_id"]] = payload["decision"]

    def rows(self):
        return [operation(row, self.decisions.get(key)) for key, row in self.calls.items()]


def operation(row, decision=None):
    inputs = row.get("input") or {}
    arguments = inputs.get("arguments", inputs)
    if not isinstance(arguments, dict):
        arguments = {}
    data = (row.get("result") or {}).get("data") or {}
    if not isinstance(data, dict):
        data = {}
    receipt = data.get("receipt") or (
        data.get("last_response") if data.get("record_state") == "response_recorded" else {})
    receipt = receipt if isinstance(receipt, dict) else {}
    target = receipt.get("target") or arguments.get("target")
    if not target and arguments.get("library"):
        target = [arguments.get(key, "") for key in ("library", "cell", "view")]
    return dict(row, request_id=data.get("request_id") or arguments.get("request_id", ""),
                target=target, receipt=receipt, code=data.get("code"), decision=decision)


def from_events(events, task_id):
    calls = ReconcileCalls()
    for event in events:
        if event.get("task_id") == task_id:
            calls.receive(event)
    return calls
