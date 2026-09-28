"""User-owned native thread operations, with durable intent and no replay."""

from ..core.contracts import json_copy
from .goals import Goals, GoalStopped
from .review import InlineReview
from .rpc import RpcError
from .thread_revert import revert_history
from .thread_memory import ThreadMemory, validate_memory


def bounded_text(value):
    if (not isinstance(value, str) or not value.strip() or "\0" in value
            or len(value) > 16000):
        raise ValueError("An explicit operation value is required")
    return value.strip()


def validate_action(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid thread operation")
    action = json_copy(value)
    kind = action.get("kind")
    fields = {
        "goal_start": {"kind", "objective", "token_budget"},
        "goal_clear": {"kind"}, "goal_pause": {"kind"}, "fork": {"kind"},
        "rollback": {"kind", "num_turns", "history_only"},
        "review": {"kind", "target"}, "compact": {"kind"},
        "memory": {"kind", "settings"},
    }
    if not isinstance(kind, str) or kind not in fields or set(action) != fields[kind]:
        raise ValueError("Unsupported thread operation fields")
    if kind == "goal_start":
        action["objective"] = bounded_text(action["objective"])
        budget = action["token_budget"]
        if budget is not None and (type(budget) is not int or not 1 <= budget <= 2**53 - 1):
            raise ValueError("Choose a positive goal token budget or explicit null for no limit")
    elif kind == "rollback":
        if (type(action["num_turns"]) is not int or not 1 <= action["num_turns"] <= 1000
                or action["history_only"] is not True):
            raise ValueError("Confirm history-only rollback and its turn count")
    elif kind == "memory":
        action["settings"] = validate_memory(action["settings"])
    elif kind == "review":
        target = action["target"]
        if not isinstance(target, dict):
            raise ValueError("Invalid review target")
        keys = {"uncommittedChanges": set(), "baseBranch": {"branch"},
                "commit": {"sha", "title"}, "custom": {"instructions"}}
        if (not isinstance(target.get("type"), str) or target["type"] not in keys
                or set(target) != {"type"} | keys[target["type"]]):
            raise ValueError("Invalid review target fields")
        for key in keys[target["type"]]:
            if key != "title" or target[key] is not None:
                target[key] = bounded_text(target[key])
    return action


class ThreadOperations:
    def __init__(self, backend, records):
        self.backend = backend
        self.goals = Goals(backend, records)
        self.memory = ThreadMemory(backend)
        self.review = InlineReview(backend)
        self.action = None
        self.receipt = None
        for event in records:
            if event["kind"] == "codex.operation.requested":
                self.receipt = {"action": event["payload"]["action"]["kind"],
                                "status": "needs_reconcile"}
            elif event["kind"] == "codex.operation.finished":
                self.receipt = {key: event["payload"][key] for key in ("action", "status")}

    def snapshot(self):
        return {"thread_id": self.backend.thread_id, **self.goals.summary(),
                "operation": json_copy(self.receipt),
                "history_mode": self.backend.history_mode,
                "memory": self.memory.snapshot()}

    def status(self):
        if self.backend.thread_id is None:
            return self.snapshot()
        self.backend._ensure_runtime()
        self.goals.read(self.backend.runtime.rpc)
        self.memory.inspect()
        return self.snapshot()

    def available(self, action):
        if self.backend.thread_id is None and action["kind"] != "goal_start":
            raise ValueError("Thread history and memory controls require an existing conversation")

    def run(self, action, cancelled, *, context, input_id):
        action = validate_action(action)
        self.available(action)
        text = action.get("objective") or "Thread operation: " + action["kind"]
        yield from self.backend.run(text, cancelled, context=context, input_id=input_id,
                                    origin="thread_operation", turn_options={}, _operation=action)
        yield from self.backend._drain()

    def prepare(self, action):
        backend = self.backend
        self.action = action
        self.receipt = {"action": action["kind"], "status": "executing"}
        backend._event("codex.operation.requested", {"action": action,
                       "thread_id": backend.thread_id, "context": backend.state.context.record()})
        if action["kind"] in {"goal_start", "memory"}:
            backend.close()
            self.goals.running = action["kind"] == "goal_start"
            self.goals.budget = None
            if action["kind"] == "memory":
                self.memory.configure(action["settings"])
            backend.active = True
            backend._tool_stop.clear()
        self.goals.turn_id = None
        self.goals.turns.clear()

    def start(self, action):
        backend, kind = self.backend, action["kind"]
        rpc, thread = backend.runtime.rpc, backend.thread_id
        if backend.cancelled() or backend.stale:
            raise GoalStopped("Operation cancelled before dispatch")
        self.goals.read(rpc)
        if kind != "goal_start":
            self.goals.pause(rpc, "explicit_operation")
        if backend.cancelled() or backend.stale:
            raise GoalStopped("Operation stopped while checking the thread")
        if kind == "goal_start":
            turn = self.goals.start(action)
        elif kind == "goal_clear":
            rpc.request("thread/goal/clear", {"threadId": thread})
            if self.goals.read(rpc) is not None:
                raise RpcError("Goal clear was not confirmed")
            turn = None
        elif kind == "goal_pause":
            turn = None
        elif kind == "memory":
            self.memory.inspect()
            turn = None
        elif kind == "fork":
            result = rpc.request("thread/fork", {"threadId": thread,
                                 "deferGoalContinuation": True,
                                 "config": {"features.goals": False}})
            fork = result.get("thread", {})
            backend._bind_thread("fork", fork, expected=thread)
            from .access_policy import configure_thread
            configure_thread(rpc, backend.thread_id, backend.turn_options.selected["access"],
                             interactive=hasattr(backend, "audit"))
            self.goals.value = None
            self.goals.before_resume(rpc)
            self.memory.apply(rpc)
            backend.native_history.sync(fork)
            turn = None
        elif kind == "rollback":
            revert_history(backend, action["num_turns"])
            turn = None
        elif kind == "review":
            turn = self.review.start(action["target"])
        else:
            rpc.request("thread/compact/start", {"threadId": thread})
            turn = self.goals.await_turn()
        backend._event("codex.operation.accepted", {"action": kind,
                       "thread_id": backend.thread_id, "turn_id": turn})
        return turn

    def consume(self, action, cancelled):
        turn = self.start(action)
        if turn is None:
            self.backend._finish("completed")
            yield from self.backend._drain()
        else:
            yield from self.backend._turn(turn, cancelled)

    def finish(self):
        self.review.clear()
        if self.action is None:
            return
        backend = self.backend
        if self.goals.running or self.memory.pending is not None:
            # Drop the thread's explicit goal capability before accepting another
            # ordinary task. Recovery pauses any unfinished goal before resume.
            backend.close()
        self.memory.pending = None
        self.goals.running = False
        self.goals.budget = None
        self.goals.objective = None
        self.goals.created_at = None
        self.goals.tokens_used = 0
        self.goals.turn_id = None
        backend._event("codex.operation.finished", {"action": self.action["kind"],
                       "status": backend.state.task.get("status"),
                       "thread_id": backend.thread_id})
        self.receipt = {"action": self.action["kind"], "status": backend.state.task.get("status")}
        self.action = None

    def interrupt(self, turn_id):
        if self.action is not None and self.action["kind"] == "review":
            self.review.interrupt(turn_id)
        else:
            self.backend.runtime.rpc.request("turn/interrupt", {
                "threadId": self.backend.thread_id, "turnId": turn_id})
