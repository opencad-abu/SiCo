"""Read-only Work/Stage/Execution tree for the task queue."""

from __future__ import annotations

from ..core.session_events import SYSTEM_ORIGINS

TERMINAL = {"completed", "failed", "cancelled", "needs_reconcile"}
BLOCKED = {"failed", "needs_reconcile"}
ACTIVE = {"queued", "executing", "waiting_user"}
MAX_EXECUTIONS = 200
MAX_WORKS = 64
MAX_STAGES = 128
MAX_CHILDREN = 20
CHILD_ORDER = {"failed": 0, "interrupted": 1, "started": 2, "completed": 3, "closed": 4}


def _text(value, limit):
    return str(value or "")[:limit]


def _execution(row):
    return {
        "id": _text(row.get("id"), 96),
        "task_id": _text(row.get("task_id"), 96),
        "text": _text(row.get("text"), 512),
        "status": _text(row.get("status"), 32),
        "origin": _text(row.get("origin"), 32),
        "diagnostic": _text(row.get("diagnostic"), 512),
    }


def _children(execution):
    rows = list((execution or {}).get("children", {}).values())
    rows.sort(key=lambda row: (
        CHILD_ORDER.get(row.get("status", ""), 5),
        str(row.get("updated_at", "")),
        str(row.get("name", "")),
    ))
    return [{
        "thread_id": _text(row.get("thread_id"), 96),
        "parent_thread_id": _text(row.get("parent_thread_id"), 96),
        "name": _text(row.get("name"), 200),
        "status": _text(row.get("status"), 32),
        "tool": _text(row.get("tool"), 64),
        "updated_at": _text(row.get("updated_at"), 64),
    } for row in rows[:MAX_CHILDREN]]


def stage_status(stage, executions):
    if stage.get("status_source") == "plan" and stage.get("status") == "planned":
        return "planned"
    if stage.get("status_source") == "model" and stage.get("status"):
        return stage["status"]
    if stage.get("last_report"):
        return "reported"
    if any(item["status"] in BLOCKED for item in executions):
        return "blocked"
    if any(item["status"] in ACTIVE for item in executions):
        return "active"
    return "in_progress"


def stage_facts(stage, executions):
    return {
        "active": any(item["status"] in ACTIVE for item in executions),
        "blocked": any(item["status"] in BLOCKED for item in executions),
        "reported": bool(stage.get("last_report")),
    }


def stage_conflict(stage, facts):
    if stage.get("status_source") != "model":
        return False
    status = stage.get("status")
    return (
        (status == "active" and facts["blocked"])
        or (status == "closed" and facts["active"])
        or (status == "reported" and not facts["reported"])
    )


def work_status(stages):
    if any(stage["status"] == "blocked" for stage in stages):
        return "blocked"
    if any(stage["status"] in {"active", "in_progress"} for stage in stages):
        return "active"
    if stages and all(stage["status"] == "planned" for stage in stages):
        return "planned"
    if stages and all(stage["status"] == "closed" for stage in stages):
        return "closed"
    if stages and all(stage["status"] == "reported" for stage in stages):
        return "reported"
    return "idle"


class SessionTaskTree:
    """Combine task history with the workbench's Work/Stage associations."""

    def __init__(self, history, workbench):
        self.history = history
        self.workbench = workbench

    def _index(self):
        if self.workbench is None:
            return None
        index = getattr(self.workbench, "index", None)
        if index is None:
            return None
        try:
            index.sync()
        except (OSError, RuntimeError, ValueError):
            return None
        return index

    def snapshot(self):
        rows = self.history.snapshot()[-MAX_EXECUTIONS:]
        index = self._index()
        executions = getattr(index, "executions", {}) if index is not None else {}
        works = getattr(index, "works", {}) if index is not None else {}
        stages = getattr(index, "stages", {}) if index is not None else {}
        plans = getattr(index, "plans", {}) if index is not None else {}
        grouped = {}
        unassigned = []

        def work_node(work_id):
            work = works.get(work_id)
            if work is None:
                return None
            return grouped.setdefault(work_id, {
                "id": work_id,
                "title": _text(work.get("title"), 200),
                "stages": {},
            })

        def stage_node(work_id, stage_id):
            source = stages.get(stage_id)
            if source is None or source.get("work_id") != work_id:
                return None
            node = work_node(work_id)
            if node is None:
                return None
            return node["stages"].setdefault(stage_id, {
                "id": stage_id,
                "title": _text(source.get("stage_title"), 200),
                "objective": _text(source.get("objective"), 2000),
                "acceptance": _text(source.get("acceptance"), 4000),
                "plan_revision": source.get("plan_revision"),
                "order": source.get("order", 0),
                "depends_on": list(source.get("depends_on") or []),
                "plan_note": _text((plans.get(work_id) or {}).get("note"), 2000),
                "status": "",
                "status_source": "",
                "reason": "",
                "blocker": "",
                "next_step": "",
                "updated_at": "",
                "facts": {},
                "conflict": False,
                "executions": [],
            })

        last_association = None
        for row in rows:
            if row.get("origin") in SYSTEM_ORIGINS:
                continue
            item = _execution(row)
            execution = executions.get(item["task_id"]) if item["task_id"] else None
            item["children"] = _children(execution)
            work_id = _text(execution.get("work_id"), 96) if execution else ""
            stage_id = _text(execution.get("stage_id"), 96) if execution else ""
            node = stage_node(work_id, stage_id)
            if node is None:
                # A follow-up task often starts before the model re-selects its
                # Work/Stage. Keep it under the previous explicit association
                # as a display-only continuation; never write this back to the
                # journal.
                if last_association is not None:
                    work_id, stage_id = last_association
                    node = stage_node(work_id, stage_id)
                if node is None:
                    unassigned.append(item)
                    continue
                item["inherited"] = True
            else:
                last_association = (work_id, stage_id)
            node["executions"].append(item)

        # Planned stages have no execution yet. Include the current plan and
        # any stage that has already been selected, reported or associated.
        for work_id in works:
            current = set((plans.get(work_id) or {}).get("stages") or [])
            existing = set((grouped.get(work_id) or {}).get("stages") or {})
            for stage_id, source in stages.items():
                if source.get("work_id") != work_id:
                    continue
                if (stage_id in current or stage_id in existing
                        or source.get("status_source") != "plan"):
                    stage_node(work_id, stage_id)

        result = []
        truncated = len(grouped) > MAX_WORKS
        stage_sequence = {stage_id: index for index, stage_id in enumerate(stages)}
        for work in list(grouped.values())[:MAX_WORKS]:
            all_stages = list(work["stages"].values())
            all_stages.sort(key=lambda row: (
                row.get("order") if isinstance(row.get("order"), int) else 10 ** 6,
                stage_sequence.get(row.get("id"), 10 ** 6),
                row.get("id", "")))
            truncated = truncated or len(all_stages) > MAX_STAGES
            stage_nodes = all_stages[:MAX_STAGES]
            for stage in stage_nodes:
                source = stages.get(stage["id"], {})
                facts = stage_facts(source, stage["executions"])
                stage.update({
                    "status": stage_status(source, stage["executions"]),
                    "status_source": _text(source.get("status_source"), 32),
                    "reason": _text(source.get("reason"), 2000),
                    "blocker": _text(source.get("blocker"), 2000),
                    "next_step": _text(source.get("next_step"), 2000),
                    "updated_at": _text(source.get("updated_at"), 64),
                    "plan_revision": source.get("plan_revision"),
                    "order": source.get("order", 0),
                    "depends_on": list(source.get("depends_on") or []),
                    "plan_note": _text((plans.get(work["id"]) or {}).get("note"), 2000),
                    "facts": facts,
                    "conflict": stage_conflict(source, facts),
                })
            work["stages"] = stage_nodes
            work["status"] = work_status(stage_nodes)
            result.append(work)
        return {
            "works": result,
            "unassigned": unassigned,
            "truncated": truncated,
        }
