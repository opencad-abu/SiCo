"""Durable watermark, runtime and queue isolation for live workbench queries."""

from sico.service.frontend_session import SessionToken

import threading

import pytest
from agent_gui_wait import receipts, settle, wait_until
from agent_replay_fixtures import Gate, blocked_append, record_task
from agent_worker_fixtures import heartbeat
from agent_worker_fixtures import window as window
from agent_window_fixture import workbench_backend

import sico.service.workbench_publication as publication
from sico.core.contracts import BoundContext
from sico.service.workbench_query import PageQuery, WorkbenchQuery
from sico.storage.history import SessionReader


@pytest.mark.parametrize("boundary", ["write", "fsync"])
def test_query_waits_off_qt_for_durable_commit_and_controls_continue(window, monkeypatch, boundary):
    controller = window.controller
    independent = window.sessions.worker.open_session("history").result(5)
    record_task(controller.journal, controller.current, "before")
    settle(window)
    source, before = window.centers.source, window.centers.index
    with blocked_append(monkeypatch, controller, boundary=boundary) as gate:
        window.centers.query_changed()
        future = window.centers._snapshot_receipt.pending[0]
        heartbeat(window)
        assert not future.done() and window.centers.index is before
        assert not gate.release.is_set()
        # The initial controller's event reader may legitimately hold its lock
        # while waiting for this journal. Use a control on the other runtime.
        command = window.sessions.worker.command(SessionToken(independent.session_id, independent.runtime_id), "initialize_context")
        assert command.result(5) is False
    receipts(window.centers)
    assert window.centers.source is source
    assert window.centers.index.version == len(controller.journal.records) > before.version


def test_new_records_between_two_pages_wait_until_next_query(window, monkeypatch):
    controller, source = window.controller, window.centers.source
    record_task(controller.journal, controller.current, "before")
    gate = Gate()
    original = publication.query_page
    owner = threading.get_ident()

    def held(index, kind, request, version):
        assert threading.get_ident() != owner
        result = original(index, kind, request, version)
        if kind == "data" and not gate.entered.is_set():
            gate.pause()
        return result

    monkeypatch.setattr(publication, "query_page", held)
    watermark = len(controller.journal.records)
    future = source.query_pages(WorkbenchQuery(query_id="before", activation=5))
    try:
        assert gate.entered.wait(5)
        record_task(controller.journal, controller.current, "after")
        heartbeat(window)
    finally:
        gate.release.set()
    version, result = future.result(5)
    assert result.version == version == watermark
    assert result.pages["data"]["total"] == 2 and result.pages["report"]["total"] == 1
    assert all(page["version"] == watermark and page["query_id"] == "before"
               for page in result.pages.values())
    version, latest = source.query_pages(WorkbenchQuery(query_id="after")).result(5)
    assert latest.pages["data"]["total"] == 4 and latest.pages["report"]["total"] == 2
    assert version == len(controller.journal.records) > watermark


def test_fixed_preview_watermark_and_live_selected_object_relocation(window):
    controller = window.controller
    for n in range(12):
        record_task(controller.journal, controller.current, "task_" + str(n))
    settle(window)
    source = window.centers.source
    initial = source.query_pages(WorkbenchQuery(data=PageQuery(page_size=5))).result(5)[1]
    key = initial.pages["data"]["rows"][4]["key"]
    preview = window.sessions.worker.preview_session(controller.session_id, 6).result(5)
    frozen = window.sessions.data.source(preview.reader, preview.events, preview.stream)
    before = frozen.query_pages(WorkbenchQuery()).result(5)[1]
    try:
        for n in range(3):
            record_task(controller.journal, controller.current, "new_" + str(n))
        request = PageQuery(page_size=5, selected_id=key, version=initial.version)
        result = source.query_pages(WorkbenchQuery(data=request)).result(5)[1]
        page = result.pages["data"]
        assert page["selected_id"] == key and page["offset"] == 10
        assert any(row["key"] == key for row in page["rows"])
        after = frozen.query_pages(WorkbenchQuery()).result(5)[1]
        assert after.version == before.version == preview.committed_sequence
        assert after.pages["data"]["total"] == before.pages["data"]["total"] == 24
        assert page["total"] == 30
        assert frozen.detail("data", key).result(5)["title"]
        assert len(controller.journal.records) > after.version
    finally:
        frozen.close()
        preview.stream.close()


@pytest.mark.parametrize("damage", ["runtime", "connection", "reopened", "closed"])
@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_stale_live_scope_discards_late_query_success_and_failure(window, monkeypatch,
                                                               damage, outcome):
    controller, view = window.controller, window.centers
    window.binding.timer.stop()
    view.processes.timer.stop()
    gate, applied = Gate(), []
    source = workbench_backend(window)
    original = source._query_pages

    def slow(request):
        result = original(request)
        gate.pause()
        if outcome == "failure":
            raise ValueError("late failure")
        return result

    monkeypatch.setattr(source, "_query_pages", slow)
    view.indexChanged.connect(applied.append)
    original_index = view.index
    view.query_changed()
    try:
        assert gate.entered.wait(5)
        if damage == "runtime":
            monkeypatch.setattr(controller, "runtime_id", "changed")
        elif damage == "connection":
            monkeypatch.setattr(controller, "current", BoundContext("other", "g2", "target", {}))
        elif damage == "reopened":
            monkeypatch.setitem(window.sessions.controllers, controller.session_id, object())
        else:
            view.source.scope.close()
        gate.release.set()
        receipts(view)
        assert not applied and view.index is original_index
        assert "查询失败" not in view.data.count.text()
    finally:
        gate.release.set()
        monkeypatch.undo()


def test_close_during_query_keeps_qt_responsive_and_retires_index_on_worker(window, monkeypatch):
    view, service = window.centers, window.sessions.data
    source, gate = workbench_backend(window), Gate()
    original = source._query_pages

    def slow(request):
        result = original(request)
        gate.pause()
        return result

    monkeypatch.setattr(source, "_query_pages", slow)
    view.query_changed()
    try:
        assert gate.entered.wait(5)
        window.request_quit()
        heartbeat(window)
        assert service.busy and view._snapshot_receipt.pending is None
    finally:
        gate.release.set()
    assert window.sessions.wait(5)
    assert source._index is None and source not in service._sources


def test_idle_worker_retires_closed_history_source(window):
    source = window.sessions.data.source(SessionReader(window.sessions.root, "history"))
    source.query_pages(WorkbenchQuery()).result(5)
    assert source._index is not None
    source.close()
    wait_until(lambda: source._index is None and source not in window.sessions.data._sources)


def test_detail_slow_read_is_independent_of_control_queue(window, monkeypatch):
    controller = window.controller
    record_task(controller.journal, controller.current, "detail")
    settle(window)
    gate, calls = Gate(), []
    source = window.centers.source
    key = source.query_pages(WorkbenchQuery()).result(5)[1].pages["data"]["rows"][0]["key"]
    backend = workbench_backend(window)
    original = backend._index.evidence_value

    def slow(key):
        calls.append(threading.get_ident())
        assert threading.get_ident() != threading.main_thread().ident
        gate.pause()
        return original(key)

    monkeypatch.setattr(backend._index, "evidence_value", slow)
    window.detail.open("data", key)
    try:
        assert gate.entered.wait(5)
        heartbeat(window)
        command = window.sessions.worker.command(SessionToken(controller.session_id, controller.runtime_id), "initialize_context")
        assert command.result(5) is False
        window.detail.invalidate()
    finally:
        gate.release.set()
    receipts(window.detail)
    assert calls and "正在读取" in window.detail.document.toPlainText()


def test_foreign_live_task_and_faulted_source_cannot_publish(window):
    source = window.centers.source
    window.binding.timer.stop()
    record_task(window.controller.journal, BoundContext("foreign", "g2", "target", {}), "bad")
    with pytest.raises(ValueError, match="another Virtuoso"):
        source.query_pages(WorkbenchQuery()).result(5)
    with pytest.raises(ValueError, match="requires reopening"):
        source.query_pages(WorkbenchQuery(query_id="retry")).result(5)
