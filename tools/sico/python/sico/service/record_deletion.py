"""Review deletion of inactive startup failures without certifying resource release."""

from ..core.contracts import identifier
from ..storage.history import SessionReader
from ..storage.roots import agent_root
from ..transport.framing import ProtocolError
from .damaged_record import inspect_record


# Legacy startup failures have no native connection or dispatched work. Unknown
# events fail closed: a missing thread ID alone cannot prove absence of work.
STARTUP_EVENTS = frozenset({
    "session.control", "binding.selected", "task.started", "task.cancelled",
    "task.needs_reconcile", "codex.resources.updated", "codex.turn.settings",
    "session.end_requested", "session.end_observed",
})


def reviewable(hub, facts, events):
    if (facts.archived or facts.deleted or not facts.snapshot or not facts.unresolved
            or facts.task.get("status") != "needs_reconcile"
            or facts.task.get("pending_calls")):
        return False
    if facts.ends:
        end = facts.ends[-1]
        if (end["kind"] != "session.end_observed" or end["payload"]["state"] != "needs_reconcile"
                or end["payload"]["address"]["service_id"] == hub.descriptor.service_id):
            return False
    if any(row["address"] and row["address"]["service_id"] == hub.descriptor.service_id
           for row in facts.inputs):
        return False
    # Legacy startup failures can lack both an end event and input service address.
    # The controller check and writer lock remain mandatory before tombstoning;
    # absence of an end never becomes evidence of successful resource release.
    tasks, unsent = set(), set()
    for row in events:
        if row["kind"] not in STARTUP_EVENTS:
            return False
        if row["kind"] == "task.started":
            tasks.add(row["task_id"])
        if row["kind"] == "codex.turn.settings":
            payload = row["payload"]
            if payload.get("status") != "not_sent" or payload.get("thread_id") is not None:
                return False
            unsent.add(row["task_id"])
    return bool(tasks) and tasks == unsent


def require_review(hub, session_id, facts, events, reviewed):
    if facts.version != reviewed:
        raise ValueError("记录已变化，请刷新后重新核对")
    if session_id in hub.owner.controllers or not reviewable(hub, facts, events):
        raise ValueError("此记录不属于未开始执行的启动失败，不能通过核对删除")


def deletion_review(hub, session_id):
    identifier(session_id)
    with hub.owner._mutation:
        if session_id in hub.owner.controllers:
            raise ValueError("活动会话必须先结束并归档，收尾完成后才能操作记录")
        facts, damaged = inspect_record(hub.owner.project, session_id)
        if damaged is not None:
            return dict(session_id=session_id, version=damaged["version"], requires_review=True,
                        task_status="", input_count=0, damaged=True)
        if facts.snapshot and facts.snapshot["project_id"] != hub.descriptor.project_id:
            raise ProtocolError("Record belongs to another project")
        required = facts.unresolved
        if required:
            reader = SessionReader(agent_root(hub.owner.project), session_id)
            require_review(hub, session_id, facts, reader.events(), facts.version)
        return dict(session_id=session_id, version=facts.version, requires_review=required,
                    task_status=facts.task.get("status", ""), input_count=len(facts.inputs))
