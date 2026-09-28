"""Worker-only filtering and deterministic pagination of workbench evidence."""

from dataclasses import dataclass

from .detail_content import CATEGORIES, STATUSES, title
from .display import event_time
from .published import freeze
from .workbench_budget import WorkbenchBudget

PAGE_LIMIT = 200
OPTION_LIMIT = 2000
SORTS = {"sequence_desc", "sequence_asc", "title_asc", "title_desc",
         "status_asc", "status_desc"}


@dataclass(frozen=True)
class PageQuery:
    keyword: str = ""
    category: str = ""
    source: str = ""
    sort: str = "sequence_desc"
    offset: int = 0
    page_size: int = PAGE_LIMIT
    selected_id: str = ""
    locate_id: str = ""
    version: int = -1

    def validate(self):
        for value in (self.keyword, self.category, self.source, self.selected_id, self.locate_id):
            if not isinstance(value, str) or len(value) > 256:
                raise ValueError("Invalid workbench filter")
        if not isinstance(self.sort, str) or self.sort not in SORTS:
            raise ValueError("Unknown workbench sort")
        if self.category and self.category not in CATEGORIES:
            raise ValueError("Unknown workbench category")
        if (type(self.offset) is not int or self.offset < 0
                or type(self.page_size) is not int or not 1 <= self.page_size <= PAGE_LIMIT
                or type(self.version) is not int):
            raise ValueError("Invalid workbench page")


@dataclass(frozen=True)
class WorkbenchQuery:
    activation: int = 0
    query_id: str = ""
    work_id: str = ""
    stage_id: str = ""
    task_id: str = ""
    data: PageQuery = PageQuery()
    report: PageQuery = PageQuery()
    audit_id: str = ""

    def validate(self):
        if type(self.activation) is not int or self.activation < 0:
            raise ValueError("Invalid workbench query identity")
        if not isinstance(self.query_id, str) or len(self.query_id) > 96:
            raise ValueError("Invalid workbench query id")
        for value in (self.work_id, self.stage_id, self.task_id, self.audit_id):
            if not isinstance(value, str) or len(value) > 256:
                raise ValueError("Invalid workbench query scope")
        for page in (self.data, self.report):
            if not isinstance(page, PageQuery):
                raise ValueError("Invalid workbench page query")
            page.validate()


def matches(row, query):
    return all(not value or row.get(key) == value for key, value in (
        ("work_id", query.work_id), ("stage_id", query.stage_id), ("task_id", query.task_id),
    ))


def page_row(row, kind, index):
    fields = ("id", "key", "category", "status", "sequence", "timestamp", "updated_at",
              "task_id", "work_id", "stage_id", "data_type", "kind", "version", "outcome")
    item = {key: row[key] for key in fields if key in row}
    item["key"] = row.get("key", row["id"])
    item["title"] = row["title"][:2000]
    item["display_title"] = title(row)[:2000]
    prefix = ("执行记录 · " if row.get("kind") == "execution" else
              "v" + str(row.get("version", "")) + " · ") if kind == "report" else (
                  CATEGORIES.get(row.get("category"), "") + " · ")
    item["label"] = prefix + item["display_title"]
    item["stage_title"] = index.stages.get(row.get("stage_id"), {}).get(
        "stage_title", "未关联阶段")[:2000]
    item["stamp"] = event_time(row.get("timestamp") or row.get("updated_at"))
    state = row.get("outcome", row.get("status", ""))
    item["state_label"] = STATUSES.get(state, state)
    return item


def _source(row):
    return row.get("tool") or row.get("data_type") or row.get("kind", "")


def query_page(index, kind, query, version):
    query.validate()
    budget = WorkbenchBudget()
    page = getattr(query, kind)
    rows = index.report_list() if kind == "report" else index.data.values()
    if kind == "report":
        target = page.locate_id or page.selected_id
        if target in index.reports and not any(row["key"] == target for row in rows):
            rows.append(index.reports[target])
    rows = [row for row in budget.rows(rows) if matches(row, query)]
    sources = sorted({_source(row) for row in budget.rows(rows) if _source(row)}
                     | ({page.source} if page.source else set()))
    if len(sources) > OPTION_LIMIT:
        raise ValueError("Workbench source options exceed the display limit")
    keyword = page.keyword.strip().casefold()
    selected = []
    for row in budget.rows(rows):
        if ((page.category and row.get("category") != page.category)
                or (page.source and _source(row) != page.source)):
            continue
        if keyword:
            display = page_row(row, kind, index)
            work = index.works.get(row.get("work_id"), {}).get("title", "")
            text = "\n".join((display["label"], display["stamp"], display["stage_title"],
                              display["state_label"], work, _source(row)))
            if keyword not in text.casefold():
                continue
        selected.append(row)

    def order(row):
        budget.checkpoint()
        key = row.get("key", row["id"])
        if page.sort.startswith("title"):
            return title(row).casefold(), row["sequence"], key
        if page.sort.startswith("status"):
            return row.get("outcome", row.get("status", "")), row["sequence"], key
        return row["sequence"], key

    selected.sort(key=order, reverse=page.sort.endswith("desc"))
    total = len(selected)
    offset = min(page.offset // page.page_size * page.page_size,
                 max(0, (total - 1) // page.page_size * page.page_size))
    # A refresh follows the selected object, even if inserts moved its page.
    target = page.locate_id or (page.selected_id if page.version != version else "")
    found = False
    if target:
        for n, row in enumerate(selected):
            if row.get("key", row["id"]) == target:
                offset, found = n // page.page_size * page.page_size, True
                break
    visible = selected[offset:offset + page.page_size]
    keys = {row.get("key", row["id"]) for row in visible}
    return freeze({
        "contract": "copilot.workbench.query.v1", "session_id": index.session_id,
        "kind": kind, "version": version, "query_id": query.query_id,
        "activation": query.activation, "total": total, "offset": offset,
        "page_size": page.page_size, "rows": tuple(page_row(r, kind, index) for r in visible),
        "selected_id": target if found else (
            page.selected_id if not page.locate_id and page.selected_id in keys else ""),
        "target_found": found, "locate_id": page.locate_id,
        "sources": tuple(sources), "categories": tuple(CATEGORIES),
    })
