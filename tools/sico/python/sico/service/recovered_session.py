"""Explicit release of recovered work; durable inputs are never replayed on attach."""

from ..core.contracts import TERMINAL, BoundContext
from ..storage.inbox_records import read_input
from .recovery_facts import read_facts


class RecoveredSession:
    def __init__(self, controller, project):
        self.controller, self.project = controller, project
        self.blocked = True
        controller.paused = True

    def stage(self, input_id):
        controller = self.controller
        if not self.blocked or controller.busy:
            raise ValueError("恢复阶段已结束，请核对原输入")
        facts = read_facts(self.project, controller.session_id)
        row = next((row for row in facts.inputs if row["input_id"] == input_id), None)
        if row is None or row["status"] != "queued":
            raise ValueError("该输入可能已开始执行，不能重放")
        record = read_input(controller.inbox.directory, input_id, confirm=True)
        if BoundContext.from_record(record["message"]["context"]) != controller.current:
            raise ValueError("输入捕获目标不同，不能转移到当前来源")
        if controller.inbox.recover(input_id):
            controller.history.record(controller.inbox.pending[-1])
            controller._changed()
        return True

    def continue_chat(self, facts=None):
        """Finish local interrupted bookkeeping without replaying any prior input."""
        controller = self.controller
        if not self.blocked:
            return
        facts = facts or read_facts(self.project, controller.session_id)
        if controller._task.get("status") == "needs_reconcile":
            controller.acknowledge_interrupted()
        for row in facts.inputs:
            self._abandon(row)
        self._release()
        controller.resume()

    def abandon(self, input_id):
        controller = self.controller
        if not self.blocked or controller.busy:
            raise ValueError("恢复阶段已结束")
        facts = read_facts(self.project, controller.session_id)
        row = next((row for row in facts.inputs if row["input_id"] == input_id), None)
        if row is None:
            return False
        return self._abandon(row)

    def _abandon(self, row):
        controller = self.controller
        if controller._task and controller._task.get("status") not in TERMINAL:
            raise ValueError("请先核对并确认原中断任务")
        controller.journal.append_session("session.input_abandoned", dict(
            input_id=row["input_id"], address=row["address"], external_outcome="unconfirmed"),
            controller.current)
        controller.inbox.pending = type(controller.inbox.pending)(
            record for record in controller.inbox.pending if record["id"] != row["input_id"])
        controller._changed()
        return True

    def release(self):
        controller = self.controller
        if not self.blocked:
            return
        facts = read_facts(self.project, controller.session_id)
        selected = {record["id"] for record in controller.inbox.pending}
        if (controller._task and controller._task.get("status") not in TERMINAL
                or any(row["status"] != "queued" or row["input_id"] not in selected
                       for row in facts.inputs)):
            raise ValueError("请先逐项恢复未开始输入或确认放弃；未知操作不会重发")
        self._release()

    def _release(self):
        controller = self.controller
        controller.bindings.events.refresh()
        if controller.bindings.sync_error or controller.current.target_id in (
                controller.bindings.events.state or {}).get("invalidated", {}):
            raise ValueError("捕获来源不可用，不能恢复队列")
        controller.journal.append_session("session.recovery_released", dict(
            runtime_id=controller.runtime_id), controller.current)
        self.blocked = False
