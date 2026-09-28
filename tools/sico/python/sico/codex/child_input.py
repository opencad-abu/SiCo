"""Live child input ownership; historical metadata never authorizes a request."""

from copy import deepcopy


class ChildInputs:
    def __init__(self, backend):
        self.backend = backend

    def scope(self, child):
        return {"thread_id": child.thread_id, "parent_thread_id": child.parent_thread_id,
                "parent_turn_id": child.parent_turn_id, "turn_id": child.turn_id,
                "task_id": child.task_id, "context": deepcopy(child.context),
                "connection_id": child.connection_id}

    def own(self, child):
        """Called only for a current parent turn's public collaboration event."""
        loop = self.backend
        if (child.parent_thread_id != loop.thread_id
                or not loop.active or not loop.runtime or not loop.steering.turn_id
                or loop.cancelled() or loop.stale or child.task_id):
            return
        child.task_id = loop.state.task["id"]
        child.context = loop.state.context.record()
        child.connection_id = loop.input_connection()
        child.parent_turn_id = loop.steering.turn_id

    def current(self, thread_id, *, turn_id=None):
        loop = self.backend
        child = loop.orchestration.children.get(thread_id)
        if child is None or thread_id == loop.thread_id:
            raise ValueError("unregistered_child")
        if child.parent_thread_id != loop.thread_id:
            raise ValueError("foreign_parent")
        if (not loop.active or loop.cancelled() or loop.stale
                or loop.state.task.get("status") != "executing"
                or child.finished or child.input_closed):
            raise ValueError("ended_child")
        if (child.task_id != loop.state.task.get("id")
                or child.context != loop.state.context.record()):
            raise ValueError("foreign_task_or_target")
        if (not loop.runtime or loop.runtime.rpc is not loop._input_rpc
                or not child.connection_id or child.connection_id != loop.connection_id):
            raise ValueError("foreign_connection")
        if turn_id is not None and (not child.turn_id or child.turn_id != turn_id):
            raise ValueError("foreign_child_turn")
        return child

    def bind(self, thread_id, turn_id):
        loop = self.backend
        child = self.current(thread_id)
        # A nullable MCP turn can use an already observed child turn, never the parent.
        effective = child.turn_id if turn_id is None else turn_id
        loop.native_history.refresh()
        if (not isinstance(effective, str) or not effective
                or (loop.thread_id, effective) in loop.native_history.origins.turns):
            raise ValueError("unverified_child_turn")
        previous = child.turn_id
        child.bind_input(task_id=loop.state.task["id"], context=loop.state.context.record(),
                         turn_id=effective, connection_id=loop.connection_id)
        if previous != child.turn_id:
            loop._event("codex.child", {
                "thread_id": child.thread_id, "parent_thread_id": child.parent_thread_id,
                "name": child.policy.name, "status": child.status, "tool": "input_binding",
                "input_scope": self.scope(child),
            })
        return child

    def active(self):
        result = []
        for child in self.backend.orchestration.children.values():
            try:
                result.append(self.current(child.thread_id))
            except ValueError:
                pass
        return result

    def settle(self, child):
        if child.finished:
            child.input_closed = True
            if hasattr(self.backend, "audit"):
                self.backend.audit.end_inputs("子任务已结束；未交付的答复不会重发",
                                              child_id=child.thread_id)
            child.clear_turn()

    def notification(self, event):
        method, params = event.get("method"), event.get("params")
        if ("id" not in event and method in {"item/started", "item/completed"}
                and isinstance(params, dict) and params.get("threadId") == self.backend.thread_id):
            item = params.get("item")
            if (isinstance(item, dict) and item.get("type") == "subAgentActivity"
                    and item.get("kind") in {"completed", "interrupted"}):
                child = self.backend.orchestration.children.get(item.get("agentThreadId"))
                if (child and child.task_id == self.backend.state.task.get("id")
                        and child.parent_turn_id == params.get("turnId")
                        and child.connection_id == self.backend.connection_id):
                    self.backend._record_subagent(item)
            # Retain the item for native evidence projection after invalidating its input.
            return False
        if ("id" in event or method not in {"turn/started", "turn/completed", "thread/closed"}
                or not isinstance(params, dict)
                or params.get("threadId") == self.backend.thread_id):
            return False
        try:
            child = self.current(params.get("threadId"))
            if method != "thread/closed":
                turn = params.get("turn")
                if not isinstance(turn, dict) or not isinstance(turn.get("id"), str):
                    raise ValueError("invalid_child_turn")
                child = self.bind(child.thread_id, turn["id"])
                if method == "turn/started":
                    return True
                status = turn.get("status")
                if status not in {"completed", "failed", "interrupted"}:
                    raise ValueError("invalid_child_status")
                child.status = status
            else:
                child.status = "closed"
            self.settle(child)
            self.backend._event("codex.child", {
                "thread_id": child.thread_id, "parent_thread_id": child.parent_thread_id,
                "name": child.policy.name, "status": child.status, "tool": method,
                "input_scope": self.scope(child),
            })
        except (ValueError, TypeError):
            self.backend._event("codex.input_ignored", {"reason": "unmatched_child_lifecycle"})
        return True
