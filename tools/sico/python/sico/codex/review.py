"""Bind inline review cancellation to its public native execution turn."""

import queue
import time
from collections import deque

from .goals import GoalStopped
from .rpc import RpcError


def identity(value):
    return isinstance(value, str) and 0 < len(value) <= 256 and "\0" not in value


class InlineReview:
    def __init__(self, backend):
        self.backend = backend
        self.binding = None
        self.rpc = None

    def scope(self):
        backend = self.backend
        return {"thread_id": backend.thread_id, "connection_id": backend.connection_id,
                "task_id": backend.state.task.get("id"),
                "context": backend.state.context.record()}

    def require_current(self, rpc, scope):
        backend = self.backend
        if (not backend.active or backend.runtime is None or backend.runtime.rpc is not rpc
                or scope != self.scope() or not identity(scope["connection_id"])):
            raise RpcError("Review connection, task or source changed; no replay")

    def start(self, target):
        self.binding, self.rpc = None, None
        rpc, scope = self.backend.runtime.rpc, self.scope()
        self.require_current(rpc, scope)
        result = rpc.request("review/start", {"threadId": scope["thread_id"],
                             "target": target, "delivery": "inline"})
        self.require_current(rpc, scope)
        turn = result.get("turn")
        if (result.get("reviewThreadId") != scope["thread_id"] or not isinstance(turn, dict)
                or not identity(turn.get("id")) or turn.get("status") != "inProgress"):
            raise RpcError("Invalid inline review receipt; no replay")
        outer = turn["id"]
        internal, entry = self.await_started(rpc, scope, outer)
        self.require_current(rpc, scope)
        binding = {**scope, "turn_id": outer, "execution_turn_id": internal,
                   "entry_item_id": entry}
        # This is cancellation provenance only. It grants no input, tool, usage
        # or history ownership and is never restored as a live capability.
        self.backend._event("codex.review.bound", binding)
        self.binding, self.rpc = binding, rpc
        return outer

    def await_started(self, rpc, scope, outer):
        backend = self.backend
        deadline = time.monotonic() + min(30, getattr(backend.provider, "idle_timeout", 30))
        retained = deque()
        entry, entered = None, False
        try:
            while time.monotonic() < deadline:
                self.require_current(rpc, scope)
                if backend.cancelled() or backend.stale:
                    raise GoalStopped("Review stopped before execution turn binding; no replay")
                try:
                    event = rpc.next()
                except queue.Empty:
                    continue
                retained.append(event)
                if len(retained) > 4096:
                    raise RpcError("Review startup notification budget exceeded; no replay")
                method, params = event.get("method"), event.get("params")
                if ("id" in event or not isinstance(params, dict)
                        or params.get("threadId") != scope["thread_id"]):
                    continue
                item = params.get("item")
                if (method in {"item/started", "item/completed"}
                        and params.get("turnId") == outer and isinstance(item, dict)
                        and item.get("type") == "enteredReviewMode"):
                    key = item.get("id")
                    if (not identity(key) or (entry is not None and key != entry)
                            or (method == "item/completed" and entry is None)):
                        raise RpcError("Invalid review entry sequence; no replay")
                    entry = key
                    entered = entered or method == "item/completed"
                elif method == "turn/started" and entry is not None:
                    turn = params.get("turn")
                    if (not entered or not isinstance(turn, dict)
                            or not identity(turn.get("id")) or turn["id"] == outer
                            or turn.get("status") != "inProgress"
                            or params.get("turnId", turn["id"]) != turn["id"]):
                        raise RpcError("Invalid review execution turn; no replay")
                    backend.native_history.refresh()
                    if (scope["thread_id"], turn["id"]) in backend.native_history.origins.turns:
                        raise RpcError("Review execution turn belongs to history; no replay")
                    return turn["id"], entry
                elif (method == "turn/completed" and isinstance(params.get("turn"), dict)
                      and params["turn"].get("id") == outer):
                    raise RpcError("Review ended without a bound execution turn; no replay")
            raise RpcError("Review execution turn did not start within its time budget; no replay")
        finally:
            # Keep the public events in their original order for the ordinary
            # turn consumer. Only that consumer may answer interactive requests.
            rpc.defer(retained)

    def interrupt(self, outer):
        binding = self.binding
        if binding is None or binding["turn_id"] != outer:
            raise RpcError("Review has no bound cancellation target; no replay")
        scope = {key: binding[key] for key in self.scope()}
        self.require_current(self.rpc, scope)
        self.backend._event("codex.review.interrupt_requested", binding)
        self.require_current(self.rpc, scope)
        self.rpc.request("turn/interrupt", {"threadId": binding["thread_id"],
                         "turnId": binding["execution_turn_id"]})

    def clear(self):
        self.binding, self.rpc = None, None
