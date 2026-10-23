"""Bind late native evidence to durable turns without changing the active task."""

from collections import ChainMap
from copy import deepcopy

ORIGIN_EVENTS = frozenset({
    "codex.thread", "task.started", "codex.turn.bound", "tool.started", "tool.finished",
    "codex.plan.updated", "codex.diff.updated", "codex.native.updated",
    "codex.connection", "codex.child",
})


def native_title(native):
    return {"commandExecution": "命令执行", "fileChange": "文件改动", "plan": "执行计划",
            "webSearch": "网页检索", "imageGeneration": "图像生成",
            "subAgentActivity": "子任务动态", "contextCompaction": "上下文压缩"}.get(
                native["item_type"], "原生记录")


class NativeOrigins:
    def __init__(self):
        self.thread_id = None
        self.tasks, self.turns, self.turn_sequences = {}, {}, {}
        self.connection_id = ""
        self.children = {}

    def observe(self, event):
        kind, payload = event["kind"], event["payload"]
        if kind == "codex.thread":
            self.thread_id = payload["thread_id"]
        if kind == "codex.connection":
            self.connection_id = payload["connection_id"]
        if kind == "task.started":
            self.tasks[event["task_id"]] = {
                "task_id": event["task_id"], "sequence": event["sequence"],
                "context": deepcopy(payload["context"]),
            }
        scope = payload.get("input_scope") if kind == "codex.child" else None
        if scope is not None:
            parent = self.origin({"thread_id": self.thread_id,
                                  "turn_id": scope.get("parent_turn_id")})
            if (not parent or payload.get("thread_id") != scope.get("thread_id")
                    or scope.get("thread_id") == self.thread_id
                    or payload.get("parent_thread_id") != self.thread_id
                    or scope.get("parent_thread_id") != self.thread_id
                    or scope.get("task_id") != event.get("task_id")
                    or scope.get("task_id") != parent["task_id"]
                    or scope.get("context") != parent["context"]
                    or not self.connection_id or scope.get("connection_id") != self.connection_id):
                raise ValueError("Child input belongs to another task or target")
            if scope.get("turn_id"):
                key = (scope["thread_id"], scope["turn_id"])
                if key in self.children and self.children[key] != scope:
                    raise ValueError("Child input turn ownership changed")
                self.children[key] = deepcopy(scope)
        native = payload.get("native")
        if (kind in ORIGIN_EVENTS
                and isinstance(native, dict) and event.get("task_id") in self.tasks):
            key = (native["thread_id"], native["turn_id"])
            task = event["task_id"]
            if key not in self.turns:
                self.turns[key] = task
                self.turn_sequences[key] = event["sequence"]
            elif self.turns[key] != task:
                self.turns[key] = None

    def copy(self):
        result = NativeOrigins()
        result.thread_id = self.thread_id
        result.tasks, result.turns = self.tasks.copy(), self.turns.copy()
        result.turn_sequences = self.turn_sequences.copy()
        result.connection_id, result.children = self.connection_id, deepcopy(self.children)
        return result

    def transaction(self):
        """Stage only this batch's additions; a bad batch cannot mutate the index."""
        result = NativeOrigins()
        result.thread_id = self.thread_id
        result.connection_id = self.connection_id
        result.children = ChainMap({}, self.children)
        result.tasks = ChainMap({}, self.tasks)
        result.turns = ChainMap({}, self.turns)
        result.turn_sequences = ChainMap({}, self.turn_sequences)
        return result

    def commit(self, staged):
        self.thread_id = staged.thread_id
        self.connection_id = staged.connection_id
        for name in ("tasks", "turns", "turn_sequences", "children"):
            getattr(self, name).update(getattr(staged, name).maps[0])

    def validate_input(self, value, task_id, *, child=False):
        if not isinstance(value, dict):
            raise ValueError("Input has invalid origin")
        if any(not isinstance(value.get(k), str) or not value[k] for k in ("thread_id", "turn_id")):
            raise ValueError("Input belongs to another task or target")
        scope = value if child else value.get("child_scope")
        if scope is not None:
            if (not isinstance(scope, dict) or any(not isinstance(scope.get(k), str)
                    for k in ("thread_id", "turn_id"))):
                raise ValueError("Input belongs to another task or target")
            key = (scope.get("thread_id"), scope.get("turn_id"))
            valid = (scope == self.children.get(key) and scope.get("task_id") == task_id)
            if not child:
                request = value.get("request", {})
                valid = (valid and value.get("thread_id") == scope.get("thread_id")
                         and value.get("turn_id") == scope.get("turn_id")
                         and value.get("task_id") == task_id
                         and value.get("context") == scope.get("context")
                         and request.get("connection_id") == scope.get("connection_id")
                         and request.get("thread_id") == scope.get("thread_id")
                         and request.get("host_turn_id") == scope.get("parent_turn_id")
                         and request.get("turn_id") in (None, scope.get("turn_id"))
                         and request.get("parent_thread_id") == scope.get("parent_thread_id")
                         and request.get("child_turn_id") == scope.get("turn_id"))
        else:
            origin = self.origin(value)
            valid = (origin and value.get("thread_id") == self.thread_id
                     and value.get("task_id") == task_id and origin["task_id"] == task_id
                     and value.get("context") == origin["context"])
        if not valid:
            raise ValueError("Input belongs to another task or target")

    def origin(self, native):
        key = (native["thread_id"], native["turn_id"])
        task = self.tasks.get(self.turns.get(key))
        return {**deepcopy(task), "turn_sequence": self.turn_sequences[key]} if task else None

    def validate(self, payload, *, require_origin=False):
        native = payload.get("native")
        if (not isinstance(native, dict) or not self.thread_id
                or native.get("thread_id") != self.thread_id
                or any(not isinstance(native.get(k), str) or not native[k]
                       for k in ("turn_id", "item_id", "item_type"))
                or payload.get("origin") != self.origin(native)
                or (require_origin and not payload.get("origin"))):
            raise ValueError("Native history origin does not match the recorded turn")
