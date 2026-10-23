"""Live child input ownership through explicit task and orchestration operations."""

from copy import deepcopy

from .input_delivery import send_controls


class ChildInputs:
    def __init__(self, *, lock, snapshot, requests, children, emit, withdraw,
                 refresh_history, known_turn, subagent):
        self._lock, self._snapshot, self._requests = lock, snapshot, requests
        self._children, self._emit, self._withdraw = children, emit, withdraw
        self._refresh_history, self._known_turn, self._subagent = refresh_history, known_turn, subagent

    def scope(self, child):
        return {"thread_id": child.thread_id, "parent_thread_id": child.parent_thread_id,
                "parent_turn_id": child.parent_turn_id, "turn_id": child.turn_id,
                "task_id": child.task_id, "context": deepcopy(child.context),
                "connection_id": child.connection_id}

    def own(self, child):
        scope = self._snapshot()
        if (child.parent_thread_id != scope.thread_id or not scope.admitted
                or not scope.turn_id or child.task_id):
            return
        connection_id = self._requests.connect()
        child.task_id, child.context = scope.task_id, scope.context
        child.connection_id, child.parent_turn_id = connection_id, scope.turn_id

    def current(self, thread_id, *, turn_id=None):
        scope = self._snapshot()
        child = self._children.get(thread_id)
        if child is None or thread_id == scope.thread_id:
            raise ValueError("unregistered_child")
        if child.parent_thread_id != scope.thread_id:
            raise ValueError("foreign_parent")
        if not scope.admitted or not scope.executing or child.finished or child.input_closed:
            raise ValueError("ended_child")
        if child.task_id != scope.task_id or child.context != scope.context:
            raise ValueError("foreign_task_or_target")
        if (not self._requests.is_current() or not child.connection_id
                or child.connection_id != self._requests.connection_id):
            raise ValueError("foreign_connection")
        if turn_id is not None and (not child.turn_id or child.turn_id != turn_id):
            raise ValueError("foreign_child_turn")
        return child

    def bind(self, thread_id, turn_id):
        child = self.current(thread_id)
        scope = self._snapshot()
        effective = child.turn_id if turn_id is None else turn_id
        self._refresh_history()
        if (not isinstance(effective, str) or not effective
                or self._known_turn(scope.thread_id, effective)):
            raise ValueError("unverified_child_turn")
        previous = child.turn_id
        child.bind_input(task_id=scope.task_id, context=scope.context,
                         turn_id=effective, connection_id=self._requests.connection_id)
        if previous != child.turn_id:
            self._emit("codex.child", {
                "thread_id": child.thread_id, "parent_thread_id": child.parent_thread_id,
                "name": child.policy.name, "status": child.status, "tool": "input_binding",
                "input_scope": self.scope(child),
            })
        return child

    def active(self):
        result = []
        for child in self._children.values():
            try:
                result.append(self.current(child.thread_id))
            except ValueError:
                pass
        return result

    def settle(self, child):
        replies = []
        if child.finished:
            child.input_closed = True
            replies = self._withdraw("子任务已结束；未交付的答复不会重发", child_id=child.thread_id)
            child.clear_turn()
        return replies

    def notification(self, event):
        method, params = event.get("method"), event.get("params")
        scope = self._snapshot()
        if ("id" not in event and method in {"item/started", "item/completed"}
                and isinstance(params, dict) and params.get("threadId") == scope.thread_id):
            item = params.get("item")
            if (isinstance(item, dict) and item.get("type") == "subAgentActivity"
                    and item.get("kind") in {"completed", "interrupted"}):
                child = self._children.get(item.get("agentThreadId"))
                if (child and child.task_id == scope.task_id
                        and child.parent_turn_id == params.get("turnId")
                        and child.connection_id == self._requests.connection_id):
                    self._subagent(item)
            return False
        with self._lock:
            handled, replies = self._lifecycle(event, method, params)
        send_controls(replies)
        return handled

    def _lifecycle(self, event, method, params):
        if ("id" in event or method not in {"turn/started", "turn/completed", "thread/closed"}
                or not isinstance(params, dict) or params.get("threadId") == self._snapshot().thread_id):
            return False, []
        try:
            child = self.current(params.get("threadId"))
            if method != "thread/closed":
                turn = params.get("turn")
                if not isinstance(turn, dict) or not isinstance(turn.get("id"), str):
                    raise ValueError("invalid_child_turn")
                child = self.bind(child.thread_id, turn["id"])
                if method == "turn/started":
                    return True, []
                status = turn.get("status")
                if status not in {"completed", "failed", "interrupted"}:
                    raise ValueError("invalid_child_status")
                child.status = status
            else:
                child.status = "closed"
            replies = self.settle(child)
            self._emit("codex.child", {
                "thread_id": child.thread_id, "parent_thread_id": child.parent_thread_id,
                "name": child.policy.name, "status": child.status, "tool": method,
                "input_scope": self.scope(child),
            })
        except (ValueError, TypeError):
            replies = []
            self._emit("codex.input_ignored", {"reason": "unmatched_child_lifecycle"})
        return True, replies
