"""Real Qt queries apply only the newest page and preserve bounded view state."""

import threading
from concurrent.futures import Future
from dataclasses import replace

import pytest
from agent_gui_wait import receipts
from agent_replay_fixtures import Gate
from agent_workbench_query_fixtures import History
from agent_worker_fixtures import heartbeat
from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtWidgets import QApplication

import sico.service.workbench_publication as publication
from sico.service.workbench_data import WorkbenchService
from sico_ui.centers import Centers
from sico_ui.workbench_view import WorkbenchDetail


@pytest.fixture
def centers():
    app = QApplication.instance() or QApplication([])
    history = History().populate(tasks=4, tools=120, reports=20)
    service = WorkbenchService()
    view = Centers(history.reader, service)
    view.resize(700, 640)
    view.show()
    try:
        receipts(view)
        yield view, history
    finally:
        view.close()
        service.close()
        assert service.wait(5)
        view.deleteLater()
        app.sendPostedEvents(None, QEvent.DeferredDelete)


def selected(listing):
    item = listing.list.currentItem()
    return item.data(0, Qt.UserRole)["key"] if item else None


@pytest.mark.parametrize("interaction", ["search", "pages", "filters"])
def test_pending_query_coalesces_to_latest_and_never_applies_old_page(centers, monkeypatch,
                                                                    interaction):
    view, _ = centers
    gate, calls, applied = Gate(), [], []
    original = view.source._query_pages
    owner = threading.get_ident()

    def blocked(request):
        assert threading.get_ident() != owner
        result = original(request)
        calls.append(request)
        if len(calls) == 1:
            gate.pause()
        return result

    monkeypatch.setattr(view.source, "_query_pages", blocked)
    view.indexChanged.connect(applied.append)
    view.data.page(1)
    try:
        assert gate.entered.wait(5)
        if interaction == "search":
            for value in ("read", "none", "measure_beta"):
                view.data.search.setText(value)
        elif interaction == "pages":
            view.data.page(1)
            view.data.page(-1)
            view.data.page(1)
        else:
            view.stage_filter.setCurrentIndex(view.stage_filter.findData("stage_0"))
            view.stage_filter.setCurrentIndex(view.stage_filter.findData("stage_2"))
        heartbeat(view)
        assert not applied and not gate.release.is_set()
    finally:
        gate.release.set()
    receipts(view)
    assert len(calls) == 2 and len(applied) == 1
    assert applied[0].query.query_id == calls[-1].query_id != calls[0].query_id
    assert view.data.total == applied[0].pages["data"]["total"]
    assert view.data.list.topLevelItemCount() <= view.data.page_size
    assert view.reports.version == view.data.version
    if interaction == "search":
        assert view.data.total == 160
        assert all(row["title"] == "measure_beta" for row in view.data.rows)
    elif interaction == "pages":
        assert view.data.offset == 400
    else:
        assert view.data.total == 142 and all(row["stage_id"] == "stage_2"
                                            for row in view.data.rows)


@pytest.mark.parametrize("transition", ["other", "same", "suspend_resume", "close", "destroy"])
def test_late_query_cannot_activate_old_page(centers, monkeypatch, transition):
    view, history = centers
    gate, applied = Gate(), []
    old_source = view.source
    original = old_source._query_pages

    def blocked(request):
        result = original(request)
        gate.pause()
        return result

    monkeypatch.setattr(old_source, "_query_pages", blocked)
    view.indexChanged.connect(applied.append)
    view.data.search.setText("read_alpha")
    assert gate.entered.wait(5)
    try:
        if transition in {"other", "same"}:
            reader = (History("other").populate(tasks=1).reader
                      if transition == "other" else history.reader)
            view.reset(view.data_service.source(reader), activation=8)
        elif transition == "suspend_resume":
            view.suspend()
            view.resume(8)
        elif transition == "close":
            view.close()
        else:
            # Exercise QObject destruction invalidation without destroying the fixture root.
            view._dispose()
        applied.clear()
        heartbeat(view)
        assert not applied
    finally:
        gate.release.set()
    receipts(view)
    if transition in {"close", "destroy"}:
        assert not applied
    else:
        assert len(applied) == 1 and applied[0].query.activation == 8
        assert applied[0].source is view.source
        assert applied[0].session_id == ("other" if transition == "other" else "query")


@pytest.mark.parametrize("damage", ["identity", "activation", "query_id", "version", "session",
                                    "page_size", "rows"])
def test_inconsistent_query_contract_is_rejected_before_render(centers, monkeypatch, damage):
    view, _ = centers
    before = view.index
    original = view.source._query_pages

    def corrupted(request):
        version, result = original(request)
        if damage == "identity":
            return version, replace(result, identity=("foreign", "runtime"))
        if damage == "activation":
            return version, replace(result, query=replace(result.query, activation=99))
        if damage == "query_id":
            return version, replace(result, query=replace(result.query, query_id="foreign"))
        field = {"version": "version", "session": "session_id", "page_size": "page_size",
                 "rows": "rows"}[damage]
        value = {"version": version + 1, "session": "foreign", "page_size": 999,
                 "rows": result.pages["data"]["rows"] * 2}[damage]
        pages = {**result.pages, "data": {**result.pages["data"], field: value}}
        return version, replace(result, pages=pages)

    monkeypatch.setattr(view.source, "_query_pages", corrupted)
    view.query_changed()
    receipts(view)
    assert view.index is before
    assert "查询失败" in view.data.count.text()
    assert not view.data.open_button.isEnabled()


def test_view_retains_scroll_and_selection_and_reveals_old_detail_pages(centers):
    view, history = centers
    view.tabs.setCurrentWidget(view.data)
    view.data.list.setCurrentItem(view.data.list.topLevelItem(60))
    bar = view.data.list.verticalScrollBar()
    bar.setValue(300)
    chosen, position = selected(view.data), bar.value()
    view.query_changed()
    receipts(view)
    assert selected(view.data) == chosen and bar.value() == position
    detail = WorkbenchDetail()
    detail.bind(view.index)
    view.indexChanged.connect(detail.bind)
    detail.objectShown.connect(view.reveal)
    detail.show()
    try:
        report = history.reports[0]
        report_key = report["id"] + "_v1"
        view.reports.page_size = 5
        view.query_changed()
        receipts(view)
        detail.open("report", report_key)
        receipts(detail)
        receipts(view)
        assert view.reports.offset > 0 and selected(view.reports) == report_key
        detail.open("data", report["evidence"][0]["id"], parent=detail.current)
        receipts(detail)
        receipts(view)
        assert selected(view.data) == report["evidence"][0]["id"]
        detail.go_back()
        receipts(detail)
        receipts(view)
        assert detail.current == ("report", report_key)
        assert selected(view.reports) == report_key
        view.show_data("unavailable")
        receipts(view)
        assert "不可用" in view.data.summary.text() and not view.data.open_button.isEnabled()
        view.data.search.setText("does not exist")
        receipts(view)
        assert view.data.total == 0 and "没有匹配" in view.data.summary.text()
    finally:
        detail.close()


def test_queryset_and_render_threads_and_large_history_heartbeat(centers, monkeypatch):
    view, _ = centers
    owner, query_threads, render_threads = threading.get_ident(), [], []
    real_query, real_render = publication.query_page, view.data.apply_page

    def query(*args):
        query_threads.append(threading.get_ident())
        assert threading.get_ident() != owner
        return real_query(*args)

    def render(page):
        render_threads.append(threading.get_ident())
        assert threading.get_ident() == owner and len(page["rows"]) <= page["page_size"]
        return real_render(page)

    monkeypatch.setattr(publication, "query_page", query)
    monkeypatch.setattr(view.data, "apply_page", render)
    view.data.search.setText("read_alpha")
    receipts(view)
    assert len(query_threads) == 2 and render_threads == [owner]


def test_runtime_validation_discards_future_delivered_after_scope_changed(centers, monkeypatch):
    view, _ = centers
    before, future = view.index, Future()
    future.set_running_or_notify_cancel()
    captured = []
    original = view.source.query_pages

    def pending(request):
        captured.append(original(request).result(5))
        return future

    monkeypatch.setattr(view.source, "query_pages", pending)
    view.query_changed()

    def invalid():
        raise ValueError("runtime changed")

    # Equivalent to a live scope refusing an old runtime at receipt time.
    monkeypatch.setattr(view.source, "validate_current", invalid)
    future.set_result(captured[0])
    receipts(view)
    assert view.index is before
    assert "查询失败" in view.data.count.text()
