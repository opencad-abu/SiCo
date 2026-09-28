"""Complete, immutable compatibility snapshots built only by the data worker."""

from dataclasses import dataclass, field

from .detail_content import title
from .published import freeze
from .pdk_update_review import project as project_audits

ROW_FIELDS = ("id", "key", "category", "status", "sequence", "timestamp", "updated_at",
              "task_id", "work_id", "stage_id", "data_type", "kind", "version", "outcome")
AUDIT_FIELDS = ("id", "title", "questions", "recommendation", "rationale", "status",
                "reason", "reply", "task_id", "stage_id", "work_id", "elicitation",
                "binding", "context")


@dataclass(frozen=True)
class WorkbenchSnapshot:
    """All presentation rows; no raw values, report bodies or mutable index."""

    source: object
    version: int = 0
    works: dict = field(default_factory=dict)
    stages: dict = field(default_factory=dict)
    data: dict = field(default_factory=dict)
    reports: dict = field(default_factory=dict)
    audits: dict = field(default_factory=dict)
    records: tuple = ()
    report_keys: tuple = ()

    def __post_init__(self):
        for name in ("works", "stages", "data", "reports", "audits", "records"):
            object.__setattr__(self, name, freeze(getattr(self, name)))

    @property
    def session_id(self):
        return self.source.session_id

    def report_list(self):
        return [self.reports[key] for key in self.report_keys]

    def association(self, task, sequence):
        for item in reversed(self.records):
            record = item.get("record", {})
            if record.get("task_id") == task and record.get("sequence", 0) <= sequence:
                return record.get("association", {"work_id": "", "stage_id": ""})
        return {"work_id": "", "stage_id": ""}

    def resolve(self, kind, key):
        rows = {"data": self.data, "report": self.reports, "audit": self.audits}.get(kind, {})
        if key not in rows:
            raise ValueError("Workbench object is unavailable")
        return rows[key]


def project_rows(rows, fields):
    # freeze() below detaches every nested mutable value before publication.
    return {key: {name: row[name] for name in fields if name in row}
            for key, row in rows.items()}


def record_event(event):
    kind, payload = event["kind"], event["payload"]
    label, markdown, audit_id = "", "", ""
    if kind == "task.started" and payload.get("origin") != "startup":
        label, markdown = "你 · " + payload["text"], payload["text"]
    elif kind == "workbench.audit.prepared":
        audit_id = payload["id"]
        label = "审阅事项 · " + payload["title"]
    elif kind == "model.completed" and payload.get("text"):
        label, markdown = "Silicon Copilot", payload["text"]
    elif kind in {"task.failed", "task.cancelled", "task.needs_reconcile"}:
        label = "任务状态"
        markdown = payload.get("message", payload.get("reason", ""))
    if label:
        record = {key: event.get(key, "") for key in
                  ("sequence", "session_id", "task_id", "timestamp")}
        return {"title": label[:2000], "record": record,
                "markdown": markdown[:100000], "audit_id": audit_id}
    return None


def publish_snapshot(source):
    index = source._index
    data, reports = project_rows(index.data, ROW_FIELDS), project_rows(index.reports, ROW_FIELDS)
    for original, rows in ((index.data, data), (index.reports, reports)):
        for key, row in rows.items():
            row["title"] = original[key]["title"][:2000]
            row["display_title"] = title(original[key])[:2000]
    for key, row in reports.items():
        row["evidence"] = index.reports[key]["evidence"]
    records = []
    for item in source._records:
        record = item["record"]
        records.append({**item, "record": {**record, "association": index.association(
            record["task_id"], record["sequence"])}})
    return WorkbenchSnapshot(
        source, source._version, project_rows(index.works, ("id", "title")),
        project_rows(index.stages, ("id", "work_id", "stage_title")), data, reports,
        project_audits(index, index.audits, AUDIT_FIELDS), tuple(records),
        tuple(row["key"] for row in index.report_list()),
    )
