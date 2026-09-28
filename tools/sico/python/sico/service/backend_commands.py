"""Optional backend resource queries and current-turn commands."""

import getpass

from ..core.contracts import json_copy


class SessionBackendCommands:
    def __init__(self, loop, *, lock, execution, check_input, binding_state, changed):
        self.loop = loop
        self._lock = lock
        self.execution = execution
        self._check_input = check_input
        self.binding_state = binding_state
        self.changed = changed

    def turn_input_status(self):
        with self._lock:
            view = self.execution()
            self._check_input("Turn input settings")
            controls = getattr(self.loop, "turn_options", None)
            if controls is None:
                raise ValueError("当前后端不支持回合设置")
            return controls.status(busy=view.busy)

    def resource_status(self):
        """Inspect registered resources only while this worker owns the idle RPC stream."""
        with self._lock:
            view = self.execution()
            if view.closing or view.shutdown:
                raise ValueError("会话正在结束")
            resources = getattr(self.loop, "resources", None)
            if resources is None:
                raise ValueError("当前后端不支持 Codex 外部资源")
            if view.busy:
                return {**json_copy(resources.published), "busy": True}
            return resources.status()

    def steer(self, text, *, scope, request_id):
        """Enqueue one supplement for the exact turn captured by the composer."""
        with self._lock:
            view = self.execution()
            self._check_input(text)
            steering = getattr(self.loop, "steering", None)
            if (steering is None or not view.busy or view.cancelled
                    or not isinstance(scope, dict) or scope.get("session_id") != view.session_id
                    or scope.get("runtime_id") != view.runtime_id):
                raise ValueError("当前会话或回合已变化；补充未发送，草稿保留")
            native = {key: value for key, value in scope.items()
                      if key not in {"session_id", "runtime_id"}}
            if native.get("context") != view.current.record():
                raise ValueError("补充来源与当前任务不一致")
            if view.current.target_id in (self.binding_state() or {}).get("invalidated", {}):
                raise ValueError("当前任务来源已失效，补充未发送")
            return steering.submit(text, native, request_id)

    def steering_scope(self):
        view = self.execution()
        task = view.task
        if (not getattr(self.loop, "steering", None) or not view.busy
                or view.closing or view.fault or view.cancelled
                or not task.get("steer_turn") or task.get("steer_blocked")
                or task.get("waiting_audits") or task.get("status") != "executing"):
            return None
        return {"session_id": view.session_id, "runtime_id": view.runtime_id,
                "thread_id": task["steer_thread"], "turn_id": task["steer_turn"],
                "task_id": task["id"], "context": view.current.record()}

    def check_target_request(self, context, task_id="", audit_id=""):
        """Validate a picker's captured origin without holding a lock during RPC."""
        with self._lock:
            view = self.execution()
            if view.closing or view.fault or view.shutdown or view.cancelled:
                raise ValueError("会话正在结束或任务已取消")
            if context.record() != view.current.record():
                raise ValueError("设计目标的来源已变化，请重新选择")
            if task_id and view.task.get("id") != task_id:
                raise ValueError("任务已变化，请重新选择设计目标")
            if audit_id and (view.task.get("id") != task_id
                             or audit_id not in view.task.get("waiting_audits", [])):
                raise ValueError("审阅事项已结束或任务已变化，请重新选择设计目标")

    def answer_audit(self, session_id, task_id, audit_id, answers, reply_id):
        with self._lock:
            view = self.execution()
            if session_id != view.session_id:
                raise ValueError("答复不属于当前会话")
            if view.closing or view.fault or view.cancelled:
                raise ValueError("会话正在结束或任务已取消")
            if task_id != view.task.get("id"):
                raise ValueError("答复所属的任务已变化")
            audit = getattr(self.loop, "audit", None)
            if audit is None:
                raise ValueError("此会话没有可答复的审阅事项")
            accepted = audit.answer(audit_id, answers, reply_id, getpass.getuser(), task_id)
            self.changed()
            return accepted

    def elicitation(self, audit_id, *, scope, response=None, reply_id=None):
        """Answer or inspect one live MCP interaction; never starts a login flow."""
        with self._lock:
            view = self.execution()
            self._check_input("MCP interaction")
            if (not view.busy or view.cancelled or not isinstance(scope, dict)
                    or scope.get("session_id") != view.session_id
                    or scope.get("runtime_id") != view.runtime_id
                    or scope.get("context") != view.current.record()
                    or view.current.target_id in (self.binding_state() or {}).get(
                        "invalidated", {})):
                raise ValueError("交互来源已变化、任务已结束或目标已失效")
            audit = getattr(self.loop, "audit", None)
            if audit is None:
                raise ValueError("当前后端不支持 MCP 交互")
            native = {k: v for k, v in scope.items() if k not in {"session_id", "runtime_id"}}
            if response is None:
                return audit.elicitations.url(audit_id, native)
            accepted = audit.elicitations.answer(
                audit_id, response, native, reply_id, getpass.getuser())
            self.changed()
            return accepted
