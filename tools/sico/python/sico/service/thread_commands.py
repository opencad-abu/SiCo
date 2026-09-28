"""Serialize explicit thread controls with the session's sole model consumer."""

class SessionThreadCommands:
    def __init__(self, loop, *, lock, execution, check_input, binding_state,
                 interrupt, changed, launch):
        self.loop = loop
        self.journal = loop.journal
        self._lock = lock
        self.execution = execution
        self._check_input = check_input
        self.binding_state = binding_state
        self.interrupt = interrupt
        self.changed = changed
        self.launch = launch

    def thread_scope(self):
        view = self.execution()
        return {"session_id": view.session_id, "runtime_id": view.runtime_id,
                "thread_id": getattr(self.loop, "thread_id", None),
                "connection_id": getattr(self.loop, "connection_id", None),
                "task_id": view.task.get("id"), "context": view.current.record()}

    def thread_status(self):
        with self._lock:
            view = self.execution()
            self._check_input("Thread status")
            controls = getattr(self.loop, "thread_ops", None)
            if controls is None:
                raise ValueError("当前后端不支持线程操作")
            snapshot = controls.snapshot() if view.busy else controls.status()
            return {**snapshot, "busy": view.busy, "scope": self.thread_scope()}

    def thread_operation(self, action, *, scope, request_id):
        from ..codex.thread_operations import validate_action

        action = validate_action(action)
        with self._lock:
            view = self.execution()
            self._check_input("Thread operation")
            controls = getattr(self.loop, "thread_ops", None)
            if (controls is None or scope != self.thread_scope()
                    or view.current.target_id in (self.binding_state() or {}).get(
                        "invalidated", {})):
                raise ValueError("线程操作的会话或来源已变化")
            controls.available(action)
            if (not isinstance(request_id, str) or not request_id or len(request_id) > 100
                    or any(e["kind"] == "codex.operation.received"
                           and e["payload"].get("request_id") == request_id
                           for e in self.journal.events())):
                raise ValueError("线程操作标识无效或已接收；不会重发")
            if view.busy:
                if action["kind"] != "goal_pause" or not controls.goals.running:
                    raise ValueError("请先结束当前任务")
                self.journal.append("codex.operation.received", {
                    "request_id": request_id, "action": action, "scope": scope,
                }, self.loop.state)
                self.interrupt()
                self.changed()
                return request_id
            if view.pending or view.task.get("status") == "needs_reconcile":
                raise ValueError("请先处理队列或核对中断任务")
            self.journal.append("codex.operation.received", {
                "request_id": request_id, "action": action, "scope": scope,
            }, self.loop.state)
            self.launch(controls, action, request_id)
            return request_id
