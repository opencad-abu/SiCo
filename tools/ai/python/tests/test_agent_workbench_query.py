"""Versioned workbench pages, worker ownership and compatibility snapshots."""

import threading
from dataclasses import replace

import pytest
from agent_workbench_query_fixtures import History

import sico.service.workbench_publication as publication
from sico.service.published import freeze
from sico.service.workbench_data import DetailLinkError, WorkbenchService
from sico.service.workbench_publication import WorkbenchQueryPublication
from sico.service.workbench_query import PageQuery, WorkbenchQuery
from sico.service.workbench_snapshot import WorkbenchSnapshot


@pytest.fixture
def service():
    worker = WorkbenchService()
    try:
        yield worker
    finally:
        worker.close()
        assert worker.wait(5)


def query(source, **kwargs):
    return source.query_pages(WorkbenchQuery(query_id="test", **kwargs)).result(5)[1]


def test_large_history_publishes_only_pages_and_keeps_complete_snapshot_api(service, monkeypatch):
    history = History().populate(tasks=8, tools=600, reports=80)
    source = service.source(history.reader, freeze(history.events))
    owner, threads = threading.get_ident(), []
    original = publication.query_page

    def in_worker(*args):
        threads.append(threading.get_ident())
        assert threading.get_ident() != owner
        return original(*args)

    monkeypatch.setattr(publication, "query_page", in_worker)
    result = query(source, activation=7, data=PageQuery(page_size=17),
                   report=PageQuery(page_size=11))
    assert isinstance(result, WorkbenchQueryPublication)
    assert len(result.data) == 17 and len(result.report_list()) == 11
    assert result.pages["data"]["total"] == 8 * (600 + 80 + 2)
    assert result.pages["report"]["total"] == 640
    assert len(threads) == 2 and len(set(threads)) == 1
    for kind in ("data", "report"):
        page = result.pages[kind]
        assert page["query_id"] == "test" and page["activation"] == 7
        assert page["version"] == result.version == len(history.events)
        assert page["session_id"] == result.session_id == "query"
        assert all("value" not in row and "evidence" not in row and "markdown" not in row
                   for row in page["rows"])
    with pytest.raises(TypeError):
        result.pages["data"]["rows"][0]["title"] = "mutated"
    _, complete = source.snapshot(-1).result(5)
    assert isinstance(complete, WorkbenchSnapshot)
    assert len(complete.data) == result.pages["data"]["total"]
    old_key = history.tools[0]["id"]
    assert complete.resolve("data", old_key)["task_id"] == "task_0"
    report = history.reports[-1]
    assert complete.resolve("report", report["id"] + "_v1")["evidence"] == report["evidence"]
    with pytest.raises(TypeError):
        complete.reports[report["id"] + "_v1"]["evidence"][0]["id"] = old_key
    assert source.snapshot(complete.version).result(5) == (complete.version, None)
    assert complete.association("task_7", len(history.events))["work_id"] == "work_1"


def test_combined_filters_options_sort_and_empty_page(service):
    history = History().populate()
    source = service.source(history.reader)
    request = PageQuery(keyword="STAGE 2", category="process", source="measure_beta",
                        sort="sequence_asc", page_size=4, offset=4)
    result = query(source, work_id="work_0", stage_id="stage_2", task_id="task_2", data=request)
    expected = [row["id"] for row in history.tools
                if row["task_id"] == "task_2" and row["name"] == "measure_beta"]
    page = result.pages["data"]
    assert page["total"] == 10 and page["offset"] == 4
    assert [row["key"] for row in page["rows"]] == expected[4:8]
    assert set(result.stages) == {"stage_0", "stage_2"}
    assert set(result.tasks) == {"task_2"}
    assert "read_alpha" in page["sources"] and "process" in page["categories"]
    assert result.pages["report"]["total"] == 4
    for sort in ("title_asc", "title_desc", "status_asc", "status_desc"):
        page = query(source, data=PageQuery(sort=sort)).pages["data"]
        values = [(row["display_title"].casefold() if sort.startswith("title") else
                   row.get("outcome", row.get("status", ""))) for row in page["rows"]]
        assert values == sorted(values, reverse=sort.endswith("desc"))
    empty = query(source, data=replace(request, keyword="no matches", offset=9999)).pages["data"]
    assert empty["rows"] == () and empty["total"] == empty["offset"] == 0


@pytest.mark.parametrize("kind", ["data", "report"])
def test_locate_selection_refresh_and_page_clamp(service, kind):
    history = History().populate(tasks=1, tools=30, reports=12)
    source = service.source(history.reader)
    key = history.tools[0]["id"] if kind == "data" else history.reports[0]["id"] + "_v1"
    result = query(source, **{kind: PageQuery(page_size=5, locate_id=key)})
    page = result.pages[kind]
    assert page["selected_id"] == key and page["target_found"] and page["offset"] > 0
    again = query(source, **{kind: PageQuery(page_size=5, selected_id=key,
                                          version=page["version"] - 1)})
    assert again.pages[kind]["offset"] == page["offset"]
    clamped = query(source, **{kind: PageQuery(page_size=5, offset=9999)})
    assert clamped.pages[kind]["offset"] == (page["total"] - 1) // 5 * 5
    missing = query(source, **{kind: PageQuery(page_size=5, locate_id="unavailable")})
    assert not missing.pages[kind]["target_found"] and not missing.pages[kind]["selected_id"]


def test_audits_are_bounded_associated_and_old_explicit_target_remains_available(service):
    history = History().populate(tasks=1, tools=1, reports=1)
    for n in range(260):
        row = {"id": f"audit_{n}", "title": "Review", "task_id": "task_0", "questions": [],
               "work_id": "work_0", "stage_id": "stage_0"}
        history.add("workbench.audit.prepared", row)
        history.add("workbench.audit.invalidated", {"id": row["id"], "reason": "closed"})
    history.add("task.started", {"text": "next task", "context": history.context.record()}, "next")
    source = service.source(history.reader)
    result = query(source, audit_id="audit_0")
    assert len(result.audits) == len(result.records) == 200 and "audit_0" in result.audits
    assert "audit_259" in result.audits
    assert all(row["record"]["association"]["stage_id"] == "stage_0" for row in result.records)
    assert query(source, work_id="work_0", task_id="task_0").audits


def test_filtered_audits_preserve_current_control_state(service):
    history = History().populate(tasks=2, tools=1, reports=1)
    for n, status in enumerate(("pending", "invalid")):
        row = {"id": f"audit_{n}", "title": "Review", "task_id": "task_1", "questions": [],
               "work_id": "work_1", "stage_id": "stage_1"}
        history.add("workbench.audit.prepared", row, "task_1")
        history.add("workbench.audit." + ("opened" if status == "pending" else "invalidated"),
                    {"id": row["id"], "reason": "closed"}, "task_1")
    source = service.source(history.reader)
    result = query(source, work_id="work_0")
    assert len(result.audits) == 2 and not result.records
    assert result.audits["audit_0"]["status"] == "pending"
    assert result.audits["audit_1"]["status"] == "invalid"


def test_explicit_execution_link_locates_a_row_hidden_by_published_report(service):
    source = service.source(History().populate(tasks=1).reader)
    ordinary = query(source)
    assert all(row["kind"] == "published" for row in ordinary.report_list())
    result = query(source, report=PageQuery(locate_id="execution_task_0"))
    assert result.pages["report"]["target_found"]
    assert result.pages["report"]["selected_id"] == "execution_task_0"


def test_detail_checks_reference_and_digest_without_a_complete_ui_index(service):
    history = History().populate(tasks=2, tools=30, reports=4)
    source = service.source(history.reader)
    result = query(source, data=PageQuery(page_size=1), report=PageQuery(page_size=1))
    report = history.reports[0]
    report_key, evidence = report["id"] + "_v1", report["evidence"][0]["id"]
    assert evidence not in {row["key"] for row in result.data}
    shown = source.detail("data", evidence, parent=("report", report_key)).result(5)
    assert "引用此数据的报告" in shown["html"]
    with pytest.raises(DetailLinkError):
        source.detail("data", history.tools[0]["id"], parent=("report", report_key)).result(5)
    with pytest.raises(DetailLinkError):
        source.detail("report", history.reports[-1]["id"] + "_v1",
                      parent=("report", report_key)).result(5)

    def damage():
        source._index.data[evidence]["value"]["data"]["payload"] = "damaged"

    service._request(damage).result(5)
    assert "校验失败" in source.detail("report", report_key).result(5)["notice"]


@pytest.mark.parametrize("damage", ["duplicate", "gap", "session"])
def test_broken_event_stream_never_publishes_partial_index(service, damage):
    history = History().populate(tasks=1, tools=1, reports=1)
    if damage == "duplicate":
        history.events.insert(2, history.events[1])
    elif damage == "gap":
        del history.events[2]
    else:
        history.events[2]["session_id"] = "foreign"
    source = service.source(history.reader)
    with pytest.raises(ValueError):
        query(source)
    with pytest.raises(ValueError, match="requires reopening"):
        query(source)


@pytest.mark.parametrize("page", [PageQuery(page_size=201), PageQuery(offset=-1),
                                  PageQuery(page_size=True), PageQuery(sort="invalid"),
                                  PageQuery(keyword="x" * 257), PageQuery(category="invalid")])
def test_invalid_queries_fail_before_indexing(service, page):
    source = service.source(History().reader)
    with pytest.raises(ValueError):
        query(source, data=page)
    assert source._index is None
