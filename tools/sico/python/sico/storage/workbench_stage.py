"""Validate and apply engineering stage selection and status facts."""

from copy import deepcopy

STAGE_STATUSES = frozenset({"active", "blocked", "reported", "closed"})
MAX_PLAN_STAGES = 32


def select_stage(stage, timestamp):
    value = deepcopy(stage)
    if value.get("status_source") == "plan" and value.get("status") == "planned":
        value["status"], value["status_source"] = "in_progress", "selection"
    else:
        value.setdefault("status", "in_progress")
        value.setdefault("status_source", "selection")
    value.setdefault("reason", "")
    value.setdefault("blocker", "")
    value.setdefault("next_step", "")
    value.setdefault("evidence_ids", [])
    value.setdefault("report_id", "")
    value["updated_at"] = timestamp
    return value


def apply_plan(plans, works, stages, payload, timestamp):
    """Persist an ordered stage plan before any execution is associated."""
    work = payload.get("work") or {}
    plan = payload.get("plan") or {}
    work_id = str(work.get("id", "")).strip()
    title = str(work.get("title", "")).strip()
    revision = plan.get("revision")
    entries = plan.get("stages")
    if not work_id or not title or len(title) > 200:
        raise ValueError("Stage plan requires a work identity and title")
    if type(revision) is not int or revision <= 0:
        raise ValueError("Stage plan revision must be a positive integer")
    previous = plans.get(work_id, {})
    if previous and revision <= int(previous.get("revision", 0)):
        raise ValueError("Stage plan revision must increase")
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_PLAN_STAGES:
        raise ValueError("Stage plan must contain 1..32 ordered stages")
    ids = [str(entry.get("id", "")).strip() for entry in entries]
    if any(not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("Stage plan IDs must be non-empty and unique")
    known = set(ids)
    normalized, titles = [], set()
    for order, entry in enumerate(entries):
        stage_id = str(entry.get("id", "")).strip()
        stage_title = str(entry.get("stage_title", "")).strip()
        objective = str(entry.get("objective", "")).strip()
        acceptance = str(entry.get("acceptance", "")).strip()
        depends = entry.get("depends_on") or []
        if not stage_title or not objective or not acceptance:
            raise ValueError("Each planned stage needs title, objective and acceptance")
        if len(stage_title) > 200 or len(objective) > 2000 or len(acceptance) > 4000:
            raise ValueError("Planned stage text exceeds its bound")
        if stage_title in titles:
            raise ValueError("Planned stage titles must be unique within a work")
        titles.add(stage_title)
        if (not isinstance(depends, list)
                or any(not isinstance(value, str) or value not in known or value == stage_id
                       for value in depends)
                or len(depends) != len(set(depends))):
            raise ValueError("Planned stage dependencies must name other stages in the plan")
        normalized.append({
            "id": stage_id,
            "stage_title": stage_title,
            "objective": objective,
            "acceptance": acceptance,
            "depends_on": list(depends),
            "order": order,
        })
    for entry in normalized:
        old = stages.get(entry["id"])
        if old is not None and old.get("work_id") != work_id:
            raise ValueError("Planned stage belongs to another work")
    works[work_id] = {**works.get(work_id, {}), "id": work_id, "title": title}
    for entry in normalized:
        old = stages.get(entry["id"], {})
        stages[entry["id"]] = {
            **old,
            "id": entry["id"],
            "work_id": work_id,
            "stage_title": entry["stage_title"],
            "objective": entry["objective"],
            "acceptance": entry["acceptance"],
            "order": entry["order"],
            "depends_on": entry["depends_on"],
            "plan_revision": revision,
            "status": old.get("status", "planned"),
            "status_source": old.get("status_source", "plan"),
            "reason": old.get("reason", ""),
            "blocker": old.get("blocker", ""),
            "next_step": old.get("next_step", ""),
            "evidence_ids": old.get("evidence_ids", []),
            "report_id": old.get("report_id", ""),
            "updated_at": old.get("updated_at") or timestamp,
        }
    plans[work_id] = {
        "revision": revision,
        "stages": ids,
        "note": str(plan.get("note", ""))[:2000],
        "updated_at": timestamp,
    }


def apply_update(stages, payload, timestamp):
    work = payload.get("work") or {}
    stage = payload.get("stage") or {}
    work_id, stage_id = work.get("id", ""), stage.get("id", "")
    status = stage.get("status")
    if (stage_id not in stages
            or stages[stage_id].get("work_id") != work_id
            or stage.get("work_id") != work_id
            or status not in STAGE_STATUSES):
        raise ValueError("Invalid stage status update")
    reason = str(stage.get("reason", ""))
    if status in {"blocked", "closed"} and not reason.strip():
        raise ValueError("Blocked or closed stage update requires a reason")
    current = stages[stage_id]
    current.update({
        "status": status,
        "status_source": "model",
        "reason": reason[:2000],
        "blocker": str(stage.get("blocker", ""))[:2000],
        "next_step": str(stage.get("next_step", ""))[:2000],
        "evidence_ids": [
            str(value)[:96] for value in stage.get("evidence_ids", [])
            if isinstance(value, str)
        ][:32],
        "report_id": str(stage.get("report_id", ""))[:96],
        "updated_at": timestamp,
    })


def record_token_usage(executions, task, payload):
    """Keep the latest committed per-task token counters for detail display."""
    execution = executions.get(task)
    if execution is None:
        return
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = payload.get(key)
        if type(value) is int and value >= 0:
            execution[key] = value
    execution["context_usage"] = payload.get("context_usage")
