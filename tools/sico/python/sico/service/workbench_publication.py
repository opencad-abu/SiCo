"""Bounded query publications, separate from complete compatibility snapshots."""

from dataclasses import dataclass, field, replace

from .published import freeze
from .workbench_query import OPTION_LIMIT, WorkbenchQuery, matches, query_page
from .workbench_snapshot import AUDIT_FIELDS
from .pdk_update_review import project as project_audits

AUDIT_LIMIT = 200


@dataclass(frozen=True)
class WorkbenchQueryPublication:
    source: object
    version: int = 0
    query: WorkbenchQuery = WorkbenchQuery()
    identity: tuple = ()
    pages: dict = field(default_factory=dict)
    works: dict = field(default_factory=dict)
    stages: dict = field(default_factory=dict)
    tasks: dict = field(default_factory=dict)
    audits: dict = field(default_factory=dict)
    records: tuple = ()
    contract: str = "copilot.workbench.pages.v1"

    def __post_init__(self):
        for name in ("works", "stages", "tasks", "audits", "records", "pages"):
            object.__setattr__(self, name, freeze(getattr(self, name)))

    @property
    def session_id(self):
        return self.identity[0]

    @property
    def data(self):
        return self.pages.get("data", {}).get("rows", ())

    def report_list(self):
        return self.pages.get("report", {}).get("rows", ())


def audit_page(index, query):
    rows = [row for row in index.audits.values() if matches(row, query)]
    # Control state must not disappear when the user filters another task.
    # Include the current execution's settled audits too: they acknowledge
    # stale waiting_audits in a session snapshot after an answer is delivered.
    current = next(reversed(index.executions), None)
    active = {row["id"]: row for row in index.audits.values()
              if row.get("status") in {"prepared", "pending", "answer_received", "dispatching"}
              or row["id"] == query.audit_id or row.get("task_id") == current}
    if len(active) > AUDIT_LIMIT:
        raise ValueError("Workbench active audits exceed the display limit")
    for row in sorted(rows, key=lambda row: row["sequence"], reverse=True):
        if len(active) == AUDIT_LIMIT:
            break
        active[row["id"]] = row
    records = []
    for row in sorted(active.values(), key=lambda row: row["sequence"]):
        if not matches(row, query) and row["id"] != query.audit_id:
            continue
        records.append({"title": row["title"][:2000], "audit_id": row["id"], "markdown": "",
                        "record": {"sequence": row["sequence"], "session_id": index.session_id,
                                   "task_id": row.get("task_id", ""),
                                   "timestamp": row.get("timestamp", ""),
                                   "association": {key: row.get(key, "")
                                                   for key in ("work_id", "stage_id")}}})
    return project_audits(index, active, AUDIT_FIELDS), tuple(records)


def publish_query(source, query):
    query.validate()
    index = source._index
    work = query.work_id if query.work_id in index.works else ""
    stages = {key: {"id": key, "work_id": row["work_id"],
                    "stage_title": row["stage_title"][:2000]}
              for key, row in index.stages.items() if not work or row["work_id"] == work}
    stage = query.stage_id if query.stage_id in stages else ""
    tasks = {key: {"id": key, "title": row["title"][:2000]}
             for key, row in index.executions.items()
             if not row["startup"] and any(
                 (not work or selection["work_id"] == work)
                 and (not stage or selection["stage_id"] == stage)
                 for selection in [row, *row["selections"]])}
    if max(len(index.works), len(stages), len(tasks)) > OPTION_LIMIT:
        raise ValueError("Workbench filter options exceed the display limit")
    query = replace(query, work_id=work, stage_id=stage,
                    task_id=query.task_id if query.task_id in tasks else "")
    # _sync() ran once. No reads or sync occur between these two pages.
    pages = {kind: query_page(index, kind, query, source._version)
             for kind in ("data", "report")}
    audits, records = audit_page(index, query)
    return WorkbenchQueryPublication(
        source, source._version, query, source.identity, pages,
        {key: {"id": key, "title": row["title"][:2000]} for key, row in index.works.items()},
        stages, tasks, audits, records,
    )
