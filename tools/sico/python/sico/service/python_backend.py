"""Compose the Python task engine with session routing, context and decisions."""

import time

from cadai.process_monitor import process_scope

from ..core.contracts import NeedsReconcile
from ..core.backend_support import PYTHON_SUPPORT
from ..core.loop import AgentLoop
from ..storage.journal import SessionJournal
from ..transport.broker_scope import skill_call_context
from .python_instructions import system_instructions
from .python_startup import task_context
from .python_target import validate as validate_target


class PythonBackend(AgentLoop):
    support = PYTHON_SUPPORT

    def __init__(self, provider, tools, journal: SessionJournal, context, config=None):
        super().__init__(provider, tools, journal, context, config, instructions=self._system)

    def execute_tool(self, call, context, cancelled=None):
        """Restore both scopes even when execution or progress publication fails."""
        with process_scope(str(self.journal.directory)), skill_call_context(
            session_id=self.journal.session_id,
            task_id=self.state.task.get("id", ""),
            tool_call_id=call.id,
            cancelled=cancelled or self.cancelled,
            progress=lambda record: self._event("router.status", record),
        ) as execution:
            return self.tools.execute(call, context, execution)

    def validate_audit_target(self):
        validate_target(self)

    def _system(self):
        return system_instructions(self.state.context, workbench=hasattr(self, "workbench"))

    def _prepare_task(self, cancelled):
        return task_context(self, cancelled)

    def _finish(self, status, message=""):
        if hasattr(self, "audit"):
            self.audit.finish()
        return super()._finish(status, message)

    def _after_tool(self, result, finished, cancelled):
        audit = getattr(self, "audit", None)
        if audit is None:
            return []
        data = result.data
        if isinstance(data, dict):
            if (data.get("schema_version") == "cad.pdk.preparation.v1"
                    and data.get("selection_required") and data.get("selection_question")):
                self.support.require("pdk_selection")
            source = f"d_{finished['sequence']}_tool"
            if (data.get("review_required") and data.get("review_kind")
                    and data.get("review_digest") and data.get("selection_question")):
                audit.require_destructive_review(data, source, self.state.context)
            if data.get("update_ref") and data.get("confirmation_required"):
                audit.require_pdk_update(data, source, self.state.context)
            if (data.get("target_ref") and data.get("decision_required")
                    and data.get("selection_question")):
                audit.require_target_choice(data, source, self.state.context)
        replies = []
        if audit.waiting_for_input() or audit.waiting_for_host():
            yield self._activity("waiting_user", "等待答复：" + audit.waiting_title())
            while audit.waiting_for_input() or audit.waiting_for_host():
                self._check_cancel(cancelled)
                replies.extend(audit.deliver_local())
                if self.stale:
                    raise NeedsReconcile(self.stale_reason)
                if audit.waiting_for_input() or audit.waiting_for_host():
                    time.sleep(0.05)
        return replies
