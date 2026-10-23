"""Plan ordered engineering stages before execution starts."""

import uuid

from ..core.contracts import ToolResult
from .workbench import WorkbenchError


def _started(service, stage_id):
    return any(
        execution.get("stage_id") == stage_id
        for execution in service.index.executions.values()
    )


def plan_stages(service, args):
    current = service.active()
    work_id = str(args.get("work_id", "")).strip()
    if current["work_id"] and work_id and work_id != current["work_id"]:
        raise WorkbenchError("Keep this execution in its selected work")
    work_id = work_id or current["work_id"]
    if work_id:
        work = service.index.works.get(work_id)
        if work is None:
            work = {"id": work_id, "title": args["work_title"]}
        elif work["title"] != args["work_title"]:
            raise WorkbenchError(
                f"Work {work_id!r} already has title {work['title']!r}; "
                "use that title or choose a new work_id"
            )
    else:
        work = {"id": "w_" + uuid.uuid4().hex, "title": args["work_title"]}
    previous = service.index.plans.get(work["id"], {})
    revision = int(previous.get("revision", 0)) + 1
    existing = {
        stage["stage_title"]: stage
        for stage in service.index.stages.values()
        if stage.get("work_id") == work["id"]
    }
    entries, titles, ids = [], set(), set()
    for item in args["stages"]:
        title = str(item["stage_title"]).strip()
        objective = str(item["objective"]).strip()
        acceptance = str(item["acceptance"]).strip()
        if not title or not objective or not acceptance:
            raise WorkbenchError("Each planned stage needs title, objective and acceptance")
        if title in titles:
            raise WorkbenchError("Planned stage titles must be unique within a work")
        titles.add(title)
        stage_id = str(item.get("stage_id", "")).strip()
        if stage_id:
            old = service.index.stages.get(stage_id)
            if old is not None and old.get("work_id") != work["id"]:
                raise WorkbenchError("stage_id belongs to another work; omit it or use a new ID")
            if old is None:
                # Models commonly invent readable IDs for a brand-new plan.
                # Accept them as new IDs, but reuse the same work's stage when
                # the title already exists (a plan revision).
                old = existing.get(title)
                if old is not None:
                    stage_id = old["id"]
        else:
            old = existing.get(title)
            stage_id = old["id"] if old else "s_" + uuid.uuid4().hex
        if stage_id in ids:
            raise WorkbenchError("Planned stage IDs must be unique")
        if old and _started(service, stage_id) and (
            old.get("stage_title") != title
            or old.get("objective") != objective
            or old.get("acceptance") != acceptance
        ):
            raise WorkbenchError("Started stage criteria cannot change")
        ids.add(stage_id)
        entries.append({
            "id": stage_id,
            "stage_title": title,
            "objective": objective,
            "acceptance": acceptance,
            "depends_on": list(item.get("depends_on") or []),
        })
    for old in existing.values():
        if _started(service, old["id"]) and old["id"] not in ids:
            raise WorkbenchError("Started stages cannot be removed from a plan")
    title_to_id = {entry["stage_title"]: entry["id"] for entry in entries}
    for entry in entries:
        resolved = []
        for dependency in entry["depends_on"]:
            stage_id = (dependency if dependency in ids
                        else title_to_id.get(str(dependency)))
            if not stage_id or stage_id == entry["id"]:
                raise WorkbenchError("Stage dependencies must name other stages in the plan")
            resolved.append(stage_id)
        entry["depends_on"] = list(dict.fromkeys(resolved))
    plan = {
        "revision": revision,
        "note": str(args.get("note", "")).strip(),
        "stages": entries,
    }
    service.emit("workbench.stages.planned", {"work": work, "plan": plan})
    return ToolResult(data={"work": work, "plan": plan})
