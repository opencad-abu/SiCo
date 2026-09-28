"""Read-only native history repair with explicit original task provenance."""

from __future__ import annotations

import json
import time

from ..storage.native_origin import NativeOrigins
from ..transport.framing import strict_json
from .native_projection import ITEMS, OUTPUT_CHARS, TOOLS, digest, operation_status, public_item


class NativeHistory:
    def __init__(self, backend, records):
        self.backend = backend
        self.origins = NativeOrigins()
        self.sequence = 0
        self.seen, self.compactions = {}, set()
        self.confirmed = set()
        self.identities = {}
        self.children = {}
        for event in records:
            self.observe(event)

    def observe(self, event):
        self.sequence = event["sequence"]
        self.origins.observe(event)
        payload = event["payload"]
        if event["kind"] == "tool.started" and payload.get("native"):
            native = payload["native"]
            key = (native["thread_id"], native["turn_id"], native["item_id"])
            self.identities[key] = native["item_type"]
        if event["kind"] not in {
            "tool.finished", "codex.plan.updated", "codex.native.updated", "codex.history.item",
        } or not payload.get("native"):
            return
        value = payload.get("result", payload.get("value", {}))
        try:
            if value.get("truncated"):
                artifact = value["artifact"]
                value = strict_json(self.backend.journal.artifact_text(
                    artifact["path"], artifact["sha256"],
                ))
            value = value.get("data", value)
            item = value.get("item")
            if not isinstance(item, dict) or item.get("type") not in ITEMS:
                return
            native = payload["native"]
            key = (native["thread_id"], native["turn_id"], native["item_id"])
            self.identities[key] = native["item_type"]
            self.seen[key] = digest([item, value.get("completion_observed", False)])
            if value.get("completion_observed"):
                self.confirmed.add(key)
            if (item["type"] == "contextCompaction" and value.get("completion_observed")
                    and value.get("source") != "thread/compacted"):
                self.compactions.add(key[:2])
        except (ValueError, OSError, KeyError, TypeError):
            # A damaged archive remains visible as damaged; repair can add a new version.
            return

    def refresh(self):
        for event in self.backend.journal.events(self.sequence):
            self.observe(event)

    def bind(self, turn_id):
        self.refresh()
        if not isinstance(turn_id, str) or not turn_id:
            raise ValueError("Missing native turn id")
        native = {"thread_id": self.backend.thread_id, "turn_id": turn_id,
                  "item_id": "", "item_type": ""}
        origin = self.origins.origin(native)
        if origin and origin["task_id"] == self.backend.state.task.get("id"):
            return
        if (native["thread_id"], turn_id) in self.origins.turns:
            raise ValueError("Native turn already belongs to another task")
        self.backend._event("codex.turn.bound", {"native": native})
        self.refresh()

    def capture(self, turn_id, item, *, observed, source="thread/history", completed_at=None):
        if not isinstance(item, dict) or item.get("type") not in ITEMS:
            return
        if not isinstance(turn_id, str) or not turn_id or not isinstance(item.get("id"), str):
            return
        if not item["id"]:
            return
        self.refresh()
        native = {"thread_id": self.backend.thread_id, "turn_id": turn_id,
                  "item_id": item["id"], "item_type": item["type"]}
        value = public_item(item)
        key = (native["thread_id"], turn_id, item["id"])
        if key in self.identities and self.identities[key] != item["type"]:
            raise ValueError("Native history item identity changed")
        if key in self.confirmed and not observed:
            return
        if self.seen.get(key) == digest([value, observed]):
            return
        # Legacy notifications have no item id. Suppress them when an actual item
        # is known; a previously archived legacy receipt cannot stand in for all
        # actual items that may later be recovered from the same turn.
        if (item["type"] == "contextCompaction" and source == "thread/compacted"
                and key[:2] in self.compactions):
            return
        origin = self.origins.origin(native)
        status = operation_status(value, observed) if item["type"] in TOOLS else (
            "recorded" if observed else "needs_reconcile"
        )
        data = {"native": native, "item": value, "completion_observed": observed,
                "origin": origin, "source": source, "completed_at": completed_at}
        self.backend._event("codex.history.item", {
            "native": native, "origin": origin, "status": status,
            "value": self.backend.native.pack(data),
        })
        if item["type"] == "subAgentActivity" and source == "late-notification":
            self.backend._record_subagent(item)
        self.refresh()

    def output(self, turn_id, item_id, delta):
        """Archive an owned command's late output without reopening its task."""
        if not isinstance(item_id, str) or not item_id or not isinstance(delta, str):
            return False
        self.refresh()
        native = {"thread_id": self.backend.thread_id, "turn_id": turn_id,
                  "item_id": item_id, "item_type": "commandExecution"}
        key = (native["thread_id"], turn_id, item_id)
        origin = self.origins.origin(native)
        if not origin or self.identities.get(key) != "commandExecution":
            return False
        if key in self.confirmed:
            return True
        for offset in range(0, len(delta), OUTPUT_CHARS):
            artifact = self.backend.journal.artifact(json.dumps({
                "native": native, "origin": origin, "text": delta[offset:offset + OUTPUT_CHARS],
            }, ensure_ascii=False).encode("utf-8"))
            self.backend._event("codex.history.output", {
                "native": native, "origin": origin, "artifact": artifact,
            })
        return True

    def capture_thread(self, thread):
        if not isinstance(thread, dict) or thread.get("id") != self.backend.thread_id:
            raise ValueError("History belongs to another thread")
        for turn in thread.get("turns", []):
            self.capture_turn(turn)

    def capture_turn(self, turn):
        observed = turn.get("status") == "completed"
        for item in turn.get("items", []):
            terminal = item.get("status") in {"completed", "failed", "declined"}
            # Shell sessions can outlive a completed model turn.
            complete = terminal if item.get("type") == "commandExecution" else observed or terminal
            self.capture(turn["id"], item, observed=complete,
                         completed_at=turn.get("completedAt"))
            if item.get("type") == "subAgentActivity":
                self.children[item.get("agentThreadId")] = item

    def pages(self, method, deadline, **params):
        cursor, visited = None, set()
        for _ in range(100):
            if time.monotonic() >= deadline:
                raise ValueError("Native history time budget exceeded")
            response = self.backend.runtime.rpc.request(method, {
                "threadId": self.backend.thread_id, "sortDirection": "asc",
                "limit": 20, "cursor": cursor, **params,
            }, timeout=min(2, max(0.1, deadline - time.monotonic())))
            yield from response["data"]
            cursor = response.get("nextCursor")
            if cursor is None:
                return
            if not isinstance(cursor, str) or cursor in visited:
                raise ValueError("Invalid native history cursor")
            visited.add(cursor)
        raise ValueError("Native history page budget exceeded")

    def sync(self, thread):
        """Resume responses normally include full history; page partial responses."""
        try:
            self.children.clear()
            if not isinstance(thread, dict) or thread.get("id") != self.backend.thread_id:
                raise ValueError("History belongs to another thread")
            if (thread.get("historyMode") == "paginated"
                    or any(t.get("itemsView", "full") != "full" for t in thread.get("turns", []))):
                deadline = time.monotonic() + 10
                for turn in self.pages("thread/turns/list", deadline, itemsView="notLoaded"):
                    for entry in self.pages("thread/items/list", deadline, turnId=turn["id"]):
                        if entry.get("turnId") != turn["id"]:
                            raise ValueError("Native history item belongs to another turn")
                        self.capture_turn({**turn, "items": [entry["item"]]})
            else:
                self.capture_thread(thread)
            for child_id, item in self.children.items():
                if child_id not in self.backend._child_status:
                    self.backend._record_subagent(item)
        except (ValueError, RuntimeError, OSError, KeyError, TypeError):
            self.backend._event("codex.history.unavailable", {
                "thread_id": self.backend.thread_id,
                "message": "原生历史补采未完成；已保存证据保留，操作未重发。",
            })

    def notification(self, event, current_turn=None):
        if "id" in event:
            return False
        params = event.get("params", {})
        if not isinstance(params, dict) or params.get("threadId") != self.backend.thread_id:
            return False
        turn_id = params.get("turnId")
        if not isinstance(turn_id, str) or not turn_id or turn_id == current_turn:
            return False
        method, item = event.get("method"), params.get("item")
        if method == "item/commandExecution/outputDelta":
            return self.output(turn_id, params.get("itemId"), params.get("delta"))
        if method == "thread/compacted":
            item = {"id": "compacted_" + turn_id, "type": "contextCompaction"}
        elif (method != "item/completed" or not isinstance(item, dict)
              or item.get("type") not in ITEMS):
            return False
        self.capture(turn_id, item, observed=True, source=(
            "thread/compacted" if method == "thread/compacted" else "late-notification"
        ))
        return True
