"""Persist public native operations; execution and model history stay in Codex."""

from __future__ import annotations

import hashlib
import json
import time

INLINE_BYTES = 8192
OUTPUT_CHARS = 16384
TOOLS = {"commandExecution": "codex_command", "fileChange": "codex_file_change",
         "webSearch": "codex_web_search", "imageGeneration": "codex_image_generation"}
STATES = {"subAgentActivity", "contextCompaction"}
ITEMS = {*TOOLS, *STATES, "plan"}
OUTPUT_METHODS = {
    "item/commandExecution/outputDelta": "commandExecution",
    "item/fileChange/outputDelta": "fileChange",
    "item/plan/delta": "plan",
}
NOTIFICATIONS = frozenset({
    "item/started", "item/completed", "turn/plan/updated", "turn/diff/updated",
    "thread/compacted", *OUTPUT_METHODS,
})


def digest(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, allow_nan=False,
    ).encode("utf-8")).hexdigest()


def public_item(item):
    kind = item["type"]
    keys = {
        "commandExecution": ("command", "cwd", "status", "exitCode", "durationMs",
                             "processId", "aggregatedOutput", "source"),
        "fileChange": ("status",),
        "plan": ("text",),
        "webSearch": ("query", "results"),
        "imageGeneration": ("status", "result", "revisedPrompt", "savedPath",
                            "transparentBackground"),
        "subAgentActivity": ("agentThreadId", "agentPath", "kind"),
        "contextCompaction": (),
    }[kind]
    result = {key: item[key] for key in keys if key in item}
    result.update(id=item["id"], type=kind)
    if kind == "webSearch" and isinstance(item.get("action"), dict):
        action = item["action"]
        keys = {"search": ("query", "queries"), "openPage": ("url",),
                "findInPage": ("url", "pattern"), "other": ()}.get(action.get("type"))
        if keys is not None:
            result["action"] = {key: action[key] for key in ("type", *keys) if key in action}
    if kind == "imageGeneration" and isinstance(item.get("failure"), dict):
        result["failure"] = {key: item["failure"][key] for key in ("type", "limitId", "resetsAt")
                             if key in item["failure"]}
    if kind == "fileChange":
        changes = item.get("changes", [])
        if not isinstance(changes, list) or any(not isinstance(c, dict) for c in changes):
            raise ValueError("Invalid native file changes")
        result["changes"] = []
        for change in changes:
            value = {key: change[key] for key in ("path", "diff") if key in change}
            if isinstance(change.get("kind"), dict):
                value["kind"] = {key: change["kind"][key] for key in ("type", "move_path")
                                 if key in change["kind"]}
            result["changes"].append(value)
    return result


def operation_status(item, observed):
    if not observed:
        return "needs_reconcile"
    kind = item["type"]
    if kind == "webSearch":
        return "ok"
    if item.get("failure") or item.get("status") in {"failed", "declined"}:
        return "tool_error"
    if item.get("status") == "completed":
        if kind in {"fileChange", "imageGeneration"}:
            return "ok"
        if kind == "commandExecution" and type(item.get("exitCode")) is int:
            return "ok" if item["exitCode"] == 0 else "tool_error"
    return "needs_reconcile"


class NativeProjection:
    def __init__(self, backend, records=()):
        self.backend = backend
        self.pending, self.completed, self.latest = {}, set(), {}
        self.compacted, self.compaction_items = set(), set()
        # Recover unfinished receipts only for the task owning the current state.
        task = backend.state.task.get("id")
        for event in records:
            if event.get("task_id") != task:
                continue
            payload = event["payload"]
            native = payload.get("native")
            if not isinstance(native, dict):
                continue
            key = self.key(native)
            if event["kind"] in {"tool.started", "codex.plan.started", "codex.native.started"}:
                self.pending[key] = self.entry(native, payload.get("input", {}))
            elif event["kind"] == "codex.item.output" and key in self.pending:
                self.pending[key]["chunks"].append(payload["artifact"])
            elif event["kind"] == "tool.finished" or (
                event["kind"] in {"codex.plan.updated", "codex.native.updated"}
                and payload.get("item_terminal")
            ):
                self.pending.pop(key, None)
                self.completed.add(key)

    @staticmethod
    def key(native):
        return (native["thread_id"], native["turn_id"], native["item_id"])

    @staticmethod
    def entry(native, inputs):
        return {"native": native, "input": inputs, "chunks": [], "buffer": "",
                "flushed_at": time.monotonic(), "started_at": time.monotonic()}

    def pack(self, value):
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) <= INLINE_BYTES:
            return value
        return {"truncated": True, "artifact": self.backend.journal.artifact(encoded)}

    def begin(self):
        self.pending.clear()
        self.completed.clear()
        self.latest.clear()
        self.compacted.clear()
        self.compaction_items.clear()

    def start(self, native, item, *, running=True):
        key = self.key(native)
        if key in self.pending and self.pending[key]["native"] != native:
            raise ValueError("Native item identity changed")
        if key in self.pending or key in self.completed:
            return
        inputs = self.pack(public_item(item))
        row = self.entry(native, inputs)
        kind = native["item_type"]
        payload = {"native": native, "input": inputs}
        if kind in TOOLS:
            payload.update(id="native_" + digest(key), name=TOOLS[kind])
            self.backend._activity("tool_call", "执行工具", tool=TOOLS[kind])
        event_kind = ("tool.started" if kind in TOOLS else
                      "codex.native.started" if kind in STATES else "codex.plan.started")
        self.backend._event(event_kind, payload)
        self.pending[key] = row
        if kind == "contextCompaction":
            self.compaction_items.add((native["thread_id"], native["turn_id"]))
        if kind == "contextCompaction" and running:
            self.snapshot("native", native, {"item": public_item(item),
                          "running": True, "completion_observed": False}, status="executing")

    def flush(self, *, force=False):
        now = time.monotonic()
        for row in self.pending.values():
            if row["buffer"] and (force or now - row["flushed_at"] >= 0.5):
                self._flush(row)

    def _flush(self, row):
        if not row["buffer"]:
            return
        artifact = self.backend.journal.artifact(json.dumps(
            {"native": row["native"], "text": row["buffer"]}, ensure_ascii=False,
        ).encode("utf-8"))
        self.backend._event("codex.item.output", {"native": row["native"], "artifact": artifact})
        row["chunks"].append(artifact)
        row["buffer"] = ""
        row["flushed_at"] = time.monotonic()

    def output(self, native, delta):
        if not isinstance(delta, str) or not delta or self.key(native) in self.completed:
            return
        self.start(native, {"id": native["item_id"], "type": native["item_type"]})
        row = self.pending[self.key(native)]
        while delta:
            size = OUTPUT_CHARS - len(row["buffer"])
            row["buffer"] += delta[:size]
            delta = delta[size:]
            if len(row["buffer"]) == OUTPUT_CHARS:
                self._flush(row)

    def finish_item(self, native, item=None):
        key = self.key(native)
        if key in self.completed:
            return
        if item is not None:
            self.start(native, item, running=False)
        row = self.pending[key]
        self._flush(row)
        observed = item is not None
        value = public_item(item) if observed else {"type": native["item_type"]}
        data = {"contract": "codex.operation.v1", "native": native, "item": value,
                "completion_observed": observed, "output_chunks": row["chunks"]}
        kind = native["item_type"]
        if kind in TOOLS:
            status = operation_status(value, observed)
            summary = {"ok": "操作已完成", "tool_error": "操作未成功",
                       "needs_reconcile": "操作结果待核对；未自动重发"}[status]
            if kind == "commandExecution" and type(value.get("exitCode")) is int:
                summary += "；退出码 " + str(value["exitCode"])
            result = self.pack({"status": status, "summary": summary,
                                "data": data, "untrusted": True})
            result.update(status=status, summary=summary)
            self.backend._event("tool.finished", {
                "id": "native_" + digest(key), "name": TOOLS[kind],
                "native": native, "result": result,
            })
        else:
            self.snapshot("native" if kind in STATES else "plan", native, data, item_terminal=True,
                          status="recorded" if observed else "needs_reconcile")
        self.pending.pop(key)
        self.completed.add(key)
        if kind == "contextCompaction" and observed:
            self.compaction_items.add((native["thread_id"], native["turn_id"]))
        if (kind in TOOLS and self.backend.active and not self.pending
                and not self.backend.state.task.get("pending_calls")
                and not self.backend.state.task.get("waiting_audits")):
            self.backend._activity("waiting_model", "等待 LLM 响应")

    def finish(self, turn_id=None):
        for row in list(self.pending.values()):
            if turn_id is None or row["native"]["turn_id"] == turn_id:
                self.finish_item(row["native"])
        for thread, turn in sorted(self.compacted - self.compaction_items):
            if turn_id is None or turn == turn_id:
                native = {"thread_id": thread, "turn_id": turn,
                          "item_id": "compacted_" + turn, "item_type": "contextCompaction"}
                self.snapshot("native", native, {
                    "item": {"id": native["item_id"], "type": "contextCompaction"},
                    "completion_observed": True, "source": "thread/compacted",
                }, item_terminal=True, status="recorded")
                self.compaction_items.add((thread, turn))

    def snapshot(self, kind, native, value, **extra):
        value = {**value, "native": native}
        key = (kind, *self.key(native))
        signature = digest(value)
        if self.latest.get(key) == signature:
            return
        payload = {"native": native, "value": self.pack(value), **extra}
        self.backend._event("codex." + kind + ".updated", payload)
        self.latest[key] = signature

    def receive(self, method, params, turn_id):
        if method not in NOTIFICATIONS:
            return False
        if (params.get("threadId") != self.backend.thread_id or params.get("turnId") != turn_id):
            return True
        native = {"thread_id": params["threadId"], "turn_id": turn_id,
                  "item_id": "", "item_type": ""}
        if method == "thread/compacted":
            self.compacted.add((params["threadId"], turn_id))
            return True
        if method in {"turn/plan/updated", "turn/diff/updated"}:
            kind = "plan" if method == "turn/plan/updated" else "diff"
            if kind == "plan":
                plan = params.get("plan")
                if not isinstance(plan, list) or any(
                    not isinstance(s, dict) or not isinstance(s.get("step"), str)
                    or s.get("status") not in {"pending", "inProgress", "completed"} for s in plan
                ):
                    return True
                value = {"plan": [{"step": s["step"], "status": s["status"]} for s in plan]}
                if isinstance(params.get("explanation"), str):
                    value["explanation"] = params["explanation"]
            else:
                if not isinstance(params.get("diff"), str):
                    return True
                value = {"diff": params["diff"]}
            self.snapshot(kind, native, value, status="recorded")
            return True
        item = params.get("item")
        if method in OUTPUT_METHODS:
            native.update(item_id=params.get("itemId"), item_type=OUTPUT_METHODS[method])
        elif isinstance(item, dict) and item.get("type") in ITEMS:
            native.update(item_id=item.get("id"), item_type=item["type"])
        else:
            return False
        if not isinstance(native["item_id"], str) or not native["item_id"]:
            return True
        if native["item_type"] == "subAgentActivity":
            if (item.get("kind") not in {"started", "interacted", "interrupted", "completed"}
                    or not isinstance(item.get("agentThreadId"), str)
                    or not item["agentThreadId"] or not isinstance(item.get("agentPath"), str)):
                return True
            self.snapshot("native", native, {"item": public_item(item),
                          "completion_observed": True}, status="recorded")
            self.backend._record_subagent(item, live=True)
            return True
        if method in OUTPUT_METHODS:
            self.output(native, params.get("delta"))
        elif method == "item/started":
            self.start(native, item)
        else:
            self.finish_item(native, item)
        return True
