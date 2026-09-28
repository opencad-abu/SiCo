"""Task journal projection and its progress clock; no transport or task scheduler."""

import json
import queue
import uuid

from ..core.contracts import ToolResult
from .backend_usage import context_usage, token_totals


class TaskJournal:
    def __init__(self, journal, state, lock, child_status, clock):
        self.journal, self.state, self.lock = journal, state, lock
        self.clock = clock
        self.child_status = child_status
        self.events = queue.Queue()
        self.last_progress = self.clock().monotonic()
        self.usage_turns = {}

    def progress(self):
        self.last_progress = self.clock().monotonic()

    def emit(self, kind, payload):
        with self.lock:
            self.last_progress = self.clock().monotonic()
            event = self.journal.append(kind, payload, self.state)
            if kind == "codex.child":
                self.child_status[payload["thread_id"]] = dict(payload)
            self.events.put(event)
            return event

    def activity(self, phase, label, *, tool=""):
        """Publish a bounded progress hint without exposing model reasoning."""
        activity = {
            "phase": phase,
            "label": label,
            "tool": tool,
            "turn": self.state.task.get("turn", 0),
            "started_at": self.clock().strftime("%Y-%m-%dT%H:%M:%SZ", self.clock().gmtime()),
        }
        self.state.task["activity"] = activity
        return self.emit("model.status", activity)

    def drain(self):
        while True:
            try:
                yield self.events.get_nowait()
            except queue.Empty:
                return

    def append_delta(self, delta):
        with self.lock:
            self.state.task["text"] += delta
            self.emit("model.delta", {"text": delta})

    def append_completed(self, previous, suffix, final):
        with self.lock:
            self.state.task["text"] += suffix
            if previous and suffix:
                self.emit("model.delta", {"text": suffix})
            self.emit("model.completed", {"text": final, "tool_calls": []})

    def record_token_usage(self, params):
        """Keep the current task's per-turn usage from Codex native updates."""
        totals = token_totals(params, self.usage_turns)
        if totals is None:
            return
        total_input, total_output = totals
        with self.lock:
            if not self.state.task:
                return
            context = context_usage(params)
            self.state.task["context_usage"] = context
            self.state.task["input_tokens"] = total_input
            self.state.task["output_tokens"] = total_output
            self.emit(
                "token.usage",
                {
                    "input_tokens": total_input,
                    "output_tokens": total_output,
                    "total_tokens": total_input + total_output,
                    "context_usage": context,
                },
            )

    def begin(self, input_id, origin, attachments, inputs, turn_options):
        self.state.task = {
            "id": uuid.uuid4().hex,
            "status": "executing",
            "input_tokens": 0,
            "output_tokens": 0,
            "pending_calls": [],
            "text": "",
            "input_id": input_id,
            "origin": origin,
            "attachments": json.loads(json.dumps(attachments or [], ensure_ascii=False)),
            "inputs": json.loads(json.dumps(inputs or [], ensure_ascii=False)),
            "turn_options": json.loads(json.dumps(turn_options or {}, ensure_ascii=False)),
        }
        self.state.messages = []


    def started(self, text, context, input_id, origin, attachments, inputs, turn_options):
        return self.emit(
            "task.started",
            {
                "text": text,
                "context": context.record(),
                "input_id": input_id,
                "origin": origin,
                "attachments": json.loads(json.dumps(attachments or [], ensure_ascii=False)),
                "inputs": json.loads(json.dumps(inputs or [], ensure_ascii=False)),
                "turn_options": json.loads(json.dumps(turn_options or {}, ensure_ascii=False)),
            },
        )

    def seal_tools(self, status, message, tool_started_at):
        pending = self.state.task.get("pending_calls", [])
        if pending:
            # A stopped MCP listener does not join its daemon handlers.
            # Seal unfinished calls before the terminal event; a later
            # receipt remains evidence and cannot reactivate this task.
            message = (message + "；" if message else "") + (
                "工具尚未返回完成回执，请核对原操作结果，不会自动重跑。"
            )
            status = "needs_reconcile"
            for call in pending:
                self.emit("tool.finished", {
                    "id": call["id"], "name": call["name"],
                    "result": ToolResult("needs_reconcile", message).record(),
                })
            self.state.task["pending_calls"] = []
            tool_started_at.clear()
        return status, message
