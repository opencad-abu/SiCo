"""Public session event batches, bound to a controller lifetime and journal cursor."""

from __future__ import annotations

from ..core.contracts import TERMINAL, BoundContext, identifier
from ..storage.native_origin import NativeOrigins

SESSION_CONTRACT = "agent_session.v1"


class EventCursor:
    def __init__(self, session_id, runtime_id, context, *, history_sequence=0):
        self.identity = (session_id, runtime_id, context.instance_id, context.generation)
        self.sequence = 0
        self.task_id = ""
        self.terminal = False
        self.history_sequence = history_sequence
        self.native_origins = NativeOrigins()
        self.calls = {}
        self.pending_calls = set()
        self.task_context = None

    def consume(self, batch):
        identity = tuple(
            batch.get(key) for key in ("session_id", "runtime_id", "instance_id", "generation")
        )
        if batch.get("contract") != SESSION_CONTRACT or identity != self.identity:
            raise ValueError("Session event belongs to another connection generation")
        if not batch["events"]:
            return []
        sequence, task_id, terminal = self.sequence, self.task_id, self.terminal
        origins = self.native_origins.transaction()
        calls, pending = self.calls.copy(), self.pending_calls.copy()
        task_context = self.task_context
        events = batch["events"]
        accepted = []
        for event in events:
            if event.get("session_id") != identity[0] or event.get("sequence") != sequence + 1:
                raise ValueError("Out-of-order session event")
            kind = event["kind"]
            if kind in {"codex.history.item", "codex.history.output"}:
                origins.validate(event["payload"], require_origin=kind == "codex.history.output")
            elicitation = event["payload"].get("elicitation_origin")
            if elicitation is not None:
                origins.validate_input(elicitation, task_id)
            child_input = event["payload"].get("input_origin")
            if child_input is not None:
                origins.validate_input(child_input, task_id, child=True)
            if kind.startswith("codex.steer."):
                scope = event["payload"].get("scope") or {}
                origin = origins.origin(scope)
                if (not origin or scope.get("task_id") != task_id
                        or scope.get("thread_id") != origins.thread_id
                        or origin["task_id"] != task_id
                        or scope.get("context") != origin["context"]):
                    raise ValueError("Supplement belongs to another task or target")
            if kind == "task.started":
                incoming = identifier(event.get("task_id"))
                if incoming == task_id or (task_id and not terminal):
                    raise ValueError("Overlapping task event")
                bound = event["payload"]["context"]
                if (event["sequence"] > self.history_sequence
                        and (bound["instance_id"], bound["generation"]) != identity[2:]):
                    raise ValueError("Task event belongs to another Virtuoso instance")
                task_id, terminal = incoming, False
                calls, pending, task_context = {}, set(), bound
            elif kind == "session.tool_receipt":
                payload = event["payload"]
                source = BoundContext.from_record(payload["source"])
                if (payload.get("task_id") != task_id or event.get("task_id") != task_id
                        or calls.get(payload.get("id")) != payload.get("name")
                        or source.record() != task_context):
                    raise ValueError("Late tool receipt belongs to another task or source")
            elif kind.startswith("binding."):
                payload = event["payload"]
                if payload.get("session_id") != identity[0]:
                    raise ValueError("Binding event belongs to another session")
                if event["sequence"] > self.history_sequence:
                    bound = (payload.get("instance_id"), payload.get("generation"))
                    if kind in {"binding.operation_started", "binding.operation_reconciled",
                                "binding.operation_rejected"} and "source" in payload:
                        # Older operation receipts carry identity only in source.
                        # Validate both forms when present; never hide a conflict.
                        source = payload["source"]
                        if not isinstance(source, dict):
                            raise ValueError("Invalid binding operation source")
                        context = BoundContext.from_record(source)
                        source_identity = (context.instance_id, context.generation)
                        if (("instance_id" in payload or "generation" in payload)
                                and bound != source_identity):
                            raise ValueError("Conflicting binding operation identity")
                        bound = source_identity
                    if bound != identity[2:]:
                        raise ValueError("Binding event belongs to another Virtuoso instance")
            elif kind.startswith((
                "task.", "model.", "tool.", "context.", "workbench.", "router.",
                "codex.item.", "codex.plan.", "codex.diff.", "codex.native.", "codex.turn.",
                "codex.steer.",
            )):
                if not task_id or event.get("task_id") != task_id:
                    raise ValueError("Stale task event")
                if terminal:
                    # Older desktops ended a Codex task before daemon MCP
                    # handlers returned. Replay only identifiable historical
                    # receipts; never relax live task or native-item ordering.
                    payload = event["payload"]
                    historical = event["sequence"] <= self.history_sequence
                    if historical and kind == "model.status":
                        sequence += 1
                        continue  # A historical activity must not restart the spinner.
                    if historical and kind == "router.status":
                        expected = {key: task_context[key]
                                    for key in ("instance_id", "generation")}
                        expected.update(session_id=identity[0], task_id=task_id)
                        valid = (payload.get("tool_call_id") in pending
                                 and all(payload.get(k) == v for k, v in expected.items()))
                    elif historical and kind == "tool.finished":
                        valid = (payload.get("id") in pending
                                 and calls.get(payload.get("id")) == payload.get("name"))
                    else:
                        valid = False
                    if not valid:
                        raise ValueError("Stale task event")
                else:
                    terminal = kind.removeprefix("task.") in TERMINAL
                if kind == "tool.started":
                    calls[event["payload"]["id"]] = event["payload"]["name"]
                    pending.add(event["payload"]["id"])
                elif kind == "tool.finished":
                    pending.discard(event["payload"]["id"])
            origins.observe(event)
            sequence += 1
            accepted.append(event)
        self.sequence, self.task_id, self.terminal = sequence, task_id, terminal
        self.native_origins.commit(origins)
        self.calls, self.pending_calls, self.task_context = calls, pending, task_context
        return accepted
