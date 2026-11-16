"""Data and detail work must stay off Qt and reject results for stale pages."""

from sico.service.frontend_session import SessionToken

import threading
from concurrent.futures import Future

import pytest
from agent_gui_wait import receipts, settle
from agent_window_fixture import workbench_backend
from agent_worker_fixtures import heartbeat
from agent_worker_fixtures import window as window
from PyQt5.QtCore import QEvent
from PyQt5.QtWidgets import QApplication, QRadioButton, QWidget

import sico.storage.workbench as workbench_module
from sico_ui.receipts import DataReceipt
from sico_ui.tool_results import ToolResults


def test_index_hashing_runs_in_data_worker_and_stale_snapshot_is_discarded(window, monkeypatch):
    preview = window.api.preview_session("history").result(5)
    loop = window.controller.loop
    loop._begin("index source", None, None)
    loop._event("task.started", {"text": "index source", "context": loop.state.context.record()})
    entered, release = threading.Event(), threading.Event()
    owner = threading.get_ident()
    digest = workbench_module.content_digest

    def slow_digest(value):
        assert threading.get_ident() != owner
        entered.set()
        assert release.wait(5)
        return digest(value)

    monkeypatch.setattr(workbench_module, "content_digest", slow_digest)
    window.centers.reset(window.api.source(window.page.session))
    stale = window.centers._snapshot_receipt.pending[0]
    try:
        assert entered.wait(5)
        heartbeat(window)
        window.centers.reset(window.api.preview_source(preview))
        assert window.centers.index.session_id == "history"
    finally:
        release.set()
    receipts(window.centers)
    assert stale.done()
    assert window.centers.index.session_id == "history"
    assert not window.centers.index.data


def test_slow_artifact_read_does_not_block_commands_or_apply_stale_selection(window, monkeypatch):
    journal = window.controller.journal
    artifact = journal.artifact(b'{"status":"ok","summary":"archived result"}')
    entered, release = threading.Event(), threading.Event()
    owner = threading.get_ident()
    read = journal.artifact_text

    def slow_read(*args, **kwargs):
        assert threading.get_ident() != owner
        entered.set()
        assert release.wait(5)
        return read(*args, **kwargs)

    monkeypatch.setattr(journal, "artifact_text", slow_read)
    view = ToolResults(lambda result, **kw: window.api.tool_result(window.page.session, result, **kw))
    try:
        view.receive("tool.started", {"id": "archive", "name": "read_results"})
        view.receive("tool.finished", {"id": "archive", "result": {
            "truncated": True, "artifact": artifact,
        }})
        assert entered.wait(5)
        heartbeat(window)
        # Metadata/control work must complete while the data queue is blocked.
        command = window.sessions.worker.command(SessionToken(window.controller.session_id, window.controller.runtime_id), "initialize_context")
        assert command.result(5) is False
        preview = window.sessions.worker.preview_session("history")
        assert preview.result(5).reader.session_id == "history"
        view.receive("tool.started", {"id": "new", "name": "read_results"})
        view.receive("tool.finished", {"id": "new", "result": {
            "status": "ok", "summary": "new selection",
        }})
        release.set()
        receipts(view)
        assert "new selection" in view.summary.toPlainText()
        assert "archived result" not in view.summary.toPlainText()
    finally:
        release.set()
        view.close()
        view.deleteLater()


def test_close_discards_pending_detail_and_waits_for_data_worker(window):
    entered, release = threading.Event(), threading.Event()
    rendered = []

    def slow_detail():
        entered.set()
        assert release.wait(5)
        return "late detail"

    future = window.sessions.data._request(slow_detail)
    window.detail._detail_receipt.watch(future, rendered.append, rendered.append)
    try:
        assert entered.wait(5)
        window.close()
        assert window.detail._detail_receipt.pending is None
        assert window.sessions.busy
        heartbeat(window)
    finally:
        release.set()
    assert window.sessions.wait(5)
    window.detail._detail_receipt.poll()
    assert not rendered


def test_audit_requested_before_snapshot_arrives_keeps_selection_and_answer_draft(
    window, monkeypatch,
):
    loop = window.controller.loop
    loop._begin("review source", None, None)
    loop._event("task.started", {"text": "review source", "context": loop.state.context.record()})
    loop._event("workbench.audit.prepared", {
        "id": "audit", "title": "Review scope", "task_id": loop.state.task["id"],
        "questions": [{"id": "scope", "header": "Scope", "question": "Which scope?",
                       "options": [{"label": "Existing results", "description": "Read only"}]}],
    })
    loop._event("workbench.audit.opened", {"id": "audit"})
    loop._event("model.completed", {"text": "Independent findings"})
    entered, release = threading.Event(), threading.Event()
    source = workbench_backend(window)
    snapshot = source._query_pages

    def slow_snapshot(version):
        entered.set()
        assert release.wait(5)
        return snapshot(version)

    monkeypatch.setattr(source, "_query_pages", slow_snapshot)
    window.centers.show_audit("audit")
    try:
        assert entered.wait(5)
        heartbeat(window)
        assert "audit" not in window.centers.index.audits
    finally:
        release.set()
    settle(window)
    panel = window.centers.audit
    assert panel.editing_audit == "audit"
    assert not panel.editor_scroll.isHidden()
    editor = panel.text_edits["scope"]
    editor.setPlainText("Retain this draft")
    choice = panel.choices.findChild(QRadioButton)
    choice.setChecked(True)
    loop._event("workbench.audit.bound", {"id": "audit"})
    settle(window)
    assert panel.text_edits["scope"] is editor
    assert editor.toPlainText() == "Retain this draft"
    assert choice.isChecked()


@pytest.mark.parametrize("running", [False, True])
def test_destroyed_receipt_owner_never_receives_late_result(running):
    widget = QWidget()
    receipt = DataReceipt(widget)
    future = Future()
    delivered = []
    if running:
        future.set_running_or_notify_cancel()
    receipt.watch(future, delivered.append, delivered.append)
    widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert receipt.pending is None
    if running:
        future.set_result("late result")
    else:
        assert future.cancelled()
    QApplication.processEvents()
    assert not delivered


def test_data_receipt_render_error_uses_failure_callback():
    widget = QWidget()
    receipt = DataReceipt(widget)
    future = Future()
    errors = []

    def broken_render(_result):
        raise ValueError("cannot render")

    receipt.watch(future, broken_render, errors.append)
    future.set_result("prepared detail")
    receipt.poll()
    assert [str(error) for error in errors] == ["cannot render"]
    assert not receipt.timer.isActive()
    widget.deleteLater()
