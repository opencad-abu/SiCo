"""Explicit goal ownership over the public 0.154 lifecycle and continuation stream."""

from __future__ import annotations

import queue
import time

from ..core.contracts import json_copy
from .rpc import RpcError

STATUSES = {"active", "paused", "blocked", "usageLimited", "budgetLimited", "complete"}
METHODS = {"thread/goal/updated", "thread/goal/cleared"}


class GoalStopped(RpcError):
    pass


def goal_value(value, thread_id):
    if value is None:
        return None
    if (not isinstance(value, dict) or value.get("threadId") != thread_id
            or "tokenBudget" not in value
            or not isinstance(value.get("status"), str)
            or value.get("status") not in STATUSES
            or not isinstance(value.get("objective"), str)
            or not 1 <= len(value["objective"]) <= 16000):
        raise ValueError("Invalid goal identity or status")
    for key in ("tokensUsed", "timeUsedSeconds", "createdAt", "updatedAt"):
        if type(value.get(key)) is not int or value[key] < 0:
            raise ValueError("Invalid goal usage")
    budget = value.get("tokenBudget")
    if budget is not None and (type(budget) is not int or budget < 1):
        raise ValueError("Invalid goal budget")
    return {key: value[key] for key in ("threadId", "objective", "status", "tokenBudget",
            "tokensUsed", "timeUsedSeconds", "createdAt", "updatedAt")}


class Goals:
    def __init__(self, backend, records):
        self.backend = backend
        self.value = None
        self.running = False
        self.budget = None
        self.objective = None
        self.created_at = None
        self.tokens_used = 0
        self.turn_id = None
        self.turns = set()
        for event in records:
            payload = event["payload"]
            if (event["kind"] in {"codex.goal.updated", "codex.goal.cleared"}
                    and payload.get("thread_id") == backend.thread_id):
                self.value = goal_value(payload.get("goal"), backend.thread_id)

    def capture(self, value, *, source, turn_id=None):
        value = goal_value(value, self.backend.thread_id)
        if self.running and self.objective is not None and value is not None:
            if self.budget is not None and (
                    value["tokenBudget"] is None or value["tokenBudget"] > self.budget):
                raise RpcError("Goal exceeded the user's authorized budget; no continuation")
            if (value["objective"] != self.objective
                    or (self.created_at is not None and value["createdAt"] != self.created_at)
                    or value["tokensUsed"] < self.tokens_used):
                raise RpcError("Goal identity or usage changed; no continuation")
            self.created_at, self.tokens_used = value["createdAt"], value["tokensUsed"]
        if value == self.value:
            return
        self.backend._event("codex.goal.updated" if value else "codex.goal.cleared", {
            "thread_id": self.backend.thread_id, "turn_id": turn_id,
            "connection_id": self.backend.connection_id, "goal": value, "source": source,
        })
        self.value = value

    def notification(self, event, *, consuming=False):
        if event.get("method") not in METHODS or "id" in event:
            return False
        params = event.get("params")
        if not isinstance(params, dict) or params.get("threadId") != self.backend.thread_id:
            return True
        turn = params.get("turnId")
        if self.running and turn is not None and turn != self.turn_id:
            # request() may read ahead of the turn consumer. Preserve an upcoming
            # turn's goal update until that consumer has bound its provenance.
            if turn in self.turns:
                return True
            if consuming:
                raise RpcError("Goal notification has no bound turn; no continuation")
            return False
        value = params.get("goal") if event["method"].endswith("updated") else None
        self.capture(value, source=event["method"], turn_id=turn)
        return True

    def read(self, rpc):
        result = rpc.request("thread/goal/get", {"threadId": self.backend.thread_id})
        self.capture(result["goal"], source="thread/goal/get")
        return json_copy(self.value)

    def pause(self, rpc, reason):
        if self.value and self.value["status"] == "active":
            self.backend._event("codex.goal.pause_requested", {
                "thread_id": self.backend.thread_id, "reason": reason,
            })
            result = rpc.request("thread/goal/set", {
                "threadId": self.backend.thread_id, "status": "paused",
            })
            self.capture(result["goal"], source=reason)
            if self.value and self.value["status"] == "active":
                raise RpcError("Goal pause was not confirmed; no replay")

    def before_resume(self, rpc):
        # These metadata RPCs work on unloaded threads. Pause before resume can
        # schedule a model turn, including after an unclean process exit.
        self.read(rpc)
        self.pause(rpc, "recovery")

    def start(self, action):
        self.turns.clear()
        self.budget = action["token_budget"]
        self.objective = action["objective"]
        self.created_at, self.tokens_used = None, 0
        rpc = self.backend.runtime.rpc
        result = rpc.request("thread/goal/set", {
            "threadId": self.backend.thread_id, "objective": action["objective"],
            "tokenBudget": action["token_budget"], "status": "active",
        })
        self.capture(result["goal"], source="user")
        return self.await_turn()

    def await_turn(self):
        backend = self.backend
        deadline = time.monotonic() + min(30, getattr(backend.provider, "idle_timeout", 30))
        while time.monotonic() < deadline:
            if backend.cancelled() or backend.stale:
                self.pause(backend.runtime.rpc, "cancelled")
                raise GoalStopped("Thread operation stopped before its turn was bound; no replay")
            try:
                event = backend.runtime.rpc.next()
            except queue.Empty:
                continue
            params = event.get("params", {})
            if (event.get("method") == "turn/started" and "id" not in event
                    and isinstance(params, dict) and params.get("threadId") == backend.thread_id):
                turn = params.get("turn", {})
                turn_id = turn.get("id")
                if (not isinstance(turn_id, str) or not turn_id or turn_id in self.turns
                        or turn.get("status") != "inProgress"):
                    raise RpcError("Invalid thread operation turn")
                return turn_id
            if "id" in event and event.get("method"):
                # No input or approval receives ownership before turn/started.
                backend.runtime.rpc.send({"id": event["id"], "error": {
                    "code": -32602, "message": "No bound operation turn"}})
            elif self.notification(event, consuming=True):
                continue
            elif not backend._notification(event):
                if (isinstance(params, dict) and params.get("threadId") == backend.thread_id
                        and event.get("method") in {"item/started", "turn/completed"}):
                    raise RpcError("Thread operation event arrived before turn/started")
        raise RpcError("Thread operation did not start within its time budget; no replay")

    def bound(self, turn_id):
        self.turn_id = turn_id
        if self.running:
            self.turns.add(turn_id)

    def continuing(self):
        return self.running and self.value is not None and self.value["status"] == "active"

    def summary(self):
        return {"goal": json_copy(self.value), "owned": self.running,
                "recovery_required": bool(self.value and self.value["status"] == "active"
                                          and not self.running)}
