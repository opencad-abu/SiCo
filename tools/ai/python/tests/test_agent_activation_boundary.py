"""Real Qt page switches keep running while journal write/fsync is paused."""

from sico.service.frontend_session import SessionToken

import gc
import threading
from concurrent.futures import Future
from dataclasses import replace

import pytest
from agent_gui_wait import activate, settle, wait_until
from agent_replay_fixtures import blocked_append, history_item, record_task
from agent_sessions_harness import DesktopSessions
from agent_window_fixture import create_window as AssistantWindow
from agent_worker_fixtures import heartbeat
from agent_worker_fixtures import window as window
from info_app_fixtures import notice_text
from PyQt5.QtCore import QEvent, QTimer
from PyQt5.QtWidgets import QApplication

from sico.core.contracts import BoundContext
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.providers.mock import ScriptedProvider
from sico.service.controller import SessionController
from sico.storage.journal import SessionJournal
from sico_ui.session_binding import SessionBinding


def test_explicit_activation_replaces_an_ended_current_page(window):
    """Ask must be able to activate after the old page was archived/closed."""
    initial = window.controller
    initial.request_close()
    assert window.api.is_closing(window.page.session)
    window.page.activate_session("history")
    settle(window)
    assert window.controller is not initial
    assert window.page.session.session_id == "history"
    assert not window.page.opening


@pytest.mark.parametrize("boundary", ["write", "fsync"])
@pytest.mark.parametrize("close_while_opening", [False, True])
def test_first_page_construction_returns_before_journal_commit(
    tmp_path, monkeypatch, boundary, close_while_opening,
):
    owner = threading.get_ident()
    context = BoundContext("instance", "generation", "target", {})
    with SessionJournal(tmp_path, "initial") as journal:
        loop = PythonBackend(ScriptedProvider([]), ToolRegistry(), journal, context)
        loop._event("session.created", {})
        journal.append("codex.thread", {"thread_id": "thread"}, loop.state)
        record_task(journal, context, "old", native=True)
        record_task(journal, context, "current", native=True)
        monkeypatch.setattr(SessionBinding, "REPLAY_MESSAGES", 1)
        controller = SessionController(loop)
        sessions = DesktopSessions(controller)
        prepared = []
        original = journal.prepare_replay

        def checked(messages, *, after=None):
            assert threading.get_ident() != owner
            result = original(messages, after=after)
            prepared.append((result[0], result[1], result[2].copy()))
            return result

        monkeypatch.setattr(journal, "prepare_replay", checked)
        widget = None
        try:
            with blocked_append(monkeypatch, controller, boundary=boundary) as gate:
                widget = AssistantWindow(controller, sessions=sessions)
                assert widget.page.opening and widget.binding.cursor is None
                assert "正在打开" in widget.status.text()
                heartbeat(widget)
                assert not gate.release.is_set() and not prepared
                assert widget.navigation.list.isEnabled()
                if close_while_opening:
                    widget.request_quit()
                    heartbeat(widget)
                    assert widget.page.receipt.pending is None
            if close_while_opening:
                assert sessions.wait(5)
                assert widget.binding.cursor is None
            else:
                settle(widget)
                assert prepared and not widget.binding.failed
                assert prepared[0][0] > 1 and set(prepared[0][2].tasks) == {"old"}
                assert widget.binding.cursor.sequence == len(journal.records)
        finally:
            sessions.close()
            assert sessions.wait(5)
            if widget is not None:
                widget.close()


@pytest.mark.parametrize("selection", ["reopen", "preview", "return", "roundtrip", "close"])
def test_page_selection_returns_and_heartbeats_before_fsync_releases(
    window, monkeypatch, selection,
):
    initial = window.controller
    activate(window, "history")
    history = window.controller
    for controller in (initial, history):
        controller.journal.append("codex.thread", {"thread_id": "thread"}, controller.loop.state)
        record_task(controller.journal, controller.current, "old", native=True)
        record_task(controller.journal, controller.current, "current", native=True)
    monkeypatch.setattr(SessionBinding, "REPLAY_MESSAGES", 1)
    activate(window, "initial")
    window.input.setPlainText("keep the live draft")
    if selection == "return":
        window.page.preview_session("history")
        settle(window)
    target = initial if selection == "return" else history
    with blocked_append(monkeypatch, target) as gate:
        if selection == "preview":
            window.page.preview_session("history")
        elif selection == "return":
            window.page.return_from_preview()
        else:
            window.page.activate_session("history")
        pending = window.page.receipt.pending[0]
        assert window.page.opening and window.controller is initial
        heartbeat(window)
        assert not gate.release.is_set() and not pending.done()
        assert window.navigation.list.isEnabled()
        if selection == "roundtrip":
            window.page.activate_session("initial")
            assert not window.page.opening and window.input.isEnabled()
            window.page.activate_session("history")
            window.page.activate_session("initial")
            window.page.activate_session("history")
            assert window.page.receipt.pending[0] is not pending
            heartbeat(window)
        elif selection == "close":
            window.request_quit()
            heartbeat(window)
            assert window.lifecycle.closing
            assert window.page.receipt.pending is None
    if selection == "close":
        assert window.sessions.wait(5)
        assert window.controller is initial
        return
    settle(window)
    assert not window.binding.failed
    assert window.controller is (history if selection in {"reopen", "roundtrip"} else initial)
    if selection == "preview":
        assert window.page.reviewing == "history" and not window.send.isEnabled()
        assert not initial.loop.provider.requests and not history.loop.provider.requests
    elif selection == "return":
        assert window.page.reviewing is None and window.input.toPlainText() == "keep the live draft"
    else:
        assert window.binding.cursor.sequence == len(history.journal.records)


def test_preview_excludes_uncommitted_line_and_does_not_execute_tools(window, monkeypatch):
    target = window.sessions.worker.open_session("history").result(5)
    with blocked_append(monkeypatch, target, kind="codex.history.unavailable",
                        payload={"message": "not durable yet"}) as gate:
        window.page.preview_session("history")
        receipt = window.page.receipt.pending[0]
        heartbeat(window)
        assert not receipt.done() and not gate.release.is_set()
        assert "not durable yet" not in window.display.toPlainText()
    settle(window)
    assert "not durable yet" in window.display.toPlainText()
    assert window.page.reviewing == "history" and window.presentation.replay.read_only
    assert not target.loop.provider.requests and not window.controller.loop.provider.requests


def test_incremental_replay_joins_preparation_without_duplicate_or_wrong_origin(
    window, monkeypatch,
):
    target = window.sessions.worker.open_session("history").result(5)
    journal = target.journal
    journal.append("codex.thread", {"thread_id": "thread"}, target.loop.state)
    record_task(journal, target.current, "old", native=True)
    record_task(journal, target.current, "current", native=True)
    monkeypatch.setattr(SessionBinding, "REPLAY_MESSAGES", 1)
    delivered = Future()
    prepared = window.sessions.worker.prepare_replay(SessionToken(target.session_id, target.runtime_id), window.page.activation + 1, 1).result(5)
    start, watermark = prepared.cursor.sequence + 1, prepared.committed_sequence
    original = window.sessions.worker.prepare_replay
    monkeypatch.setattr(window.sessions.worker, "prepare_replay", lambda *args: delivered)
    received = []
    receive = window.renderer.receive

    def track(event):
        received.append(event)
        receive(event)

    monkeypatch.setattr(window.renderer, "receive", track)
    window.page.activate_session("history")
    journal.append("codex.history.item", history_item(journal), target.loop.state)
    delivered.set_result(prepared)
    wait_until(lambda: not window.page.opening)
    record_task(journal, target.current, "next", native=True)
    window.binding.poll()
    monkeypatch.setattr(window.sessions.worker, "prepare_replay", original)
    settle(window)
    assert window.controller is target and not window.binding.failed
    assert [event["sequence"] for event in received] == list(range(start, len(journal.records) + 1))
    assert window.binding.cursor.sequence > watermark
    origin = prepared.stream.cursor.native_origins.origin(history_item(journal)["native"])
    assert origin["task_id"] == "old"


@pytest.mark.parametrize("damage", ["activation", "runtime", "session", "replacement", "closed"])
def test_prepared_result_identity_is_checked_before_page_activation(window, monkeypatch, damage):
    target = window.sessions.worker.open_session("history").result(5)
    prepared = window.sessions.worker.prepare_replay(SessionToken(target.session_id, target.runtime_id), window.page.activation + 1, 1).result(5)
    future = Future()
    monkeypatch.setattr(window.sessions.worker, "prepare_replay", lambda *args: future)
    initial = window.controller
    window.page.activate_session("history")
    if damage == "activation":
        prepared = replace(prepared, activation=prepared.activation - 1)
    elif damage in {"runtime", "session"}:
        identity = list(prepared.cursor.identity)
        identity[1 if damage == "runtime" else 0] = "foreign"
        prepared.cursor.identity = tuple(identity)
    elif damage == "replacement":
        window.sessions.controllers["history"] = initial
    else:
        target.request_close()
    try:
        future.set_result(prepared)
        wait_until(lambda: not window.page.opening)
        assert window.controller is initial
        assert "失效" in notice_text(window)
        assert "失效" not in window.status.text()
        assert window.input.isEnabled()
    finally:
        window.sessions.controllers["history"] = target


@pytest.mark.parametrize("outcome", ["success", "failure"])
@pytest.mark.parametrize("preview", [False, True])
def test_late_preparation_cannot_override_new_selection(window, monkeypatch, outcome, preview):
    target = window.sessions.worker.open_session("history").result(5)
    activation = window.page.activation + 1
    if preview:
        prepared = window.sessions.worker.preview_session("history", activation).result(5)
    else:
        prepared = window.sessions.worker.prepare_replay(SessionToken(target.session_id, target.runtime_id), activation, 1).result(5)
    future = Future()
    future.set_running_or_notify_cancel()
    method = "preview_session" if preview else "prepare_replay"
    monkeypatch.setattr(window.sessions.worker, method, lambda *args: future)
    select = window.page.preview_session if preview else window.page.activate_session
    select("history")
    window.page.activate_session("initial")
    settle(window)
    status = window.status.text()
    if outcome == "failure":
        future.set_exception(OSError("obsolete error"))
    else:
        future.set_result(prepared)
    heartbeat(window)
    assert window.controller.session_id == "initial" and not window.page.opening
    assert window.status.text() == status and window.input.isEnabled()


@pytest.mark.parametrize("damage", ["activation", "runtime", "session", "opened"])
def test_preview_preparation_rejects_changed_page_and_runtime(window, monkeypatch, damage):
    prepared = window.sessions.worker.preview_session("history", window.page.activation + 1).result(5)
    future = Future()
    monkeypatch.setattr(window.sessions.worker, "preview_session", lambda *args: future)
    window.page.preview_session("history")
    if damage == "opened":
        window.sessions.worker.open_session("history").result(5)
    elif damage == "activation":
        prepared = replace(prepared, activation=prepared.activation - 1)
    else:
        prepared = replace(prepared, **{damage + "_id": "foreign"})
    sources = []
    reset = window.centers.reset

    def track(source, **kwargs):
        sources.append(source.session_id)
        reset(source, **kwargs)

    monkeypatch.setattr(window.centers, "reset", track)
    future.set_result(prepared)
    settle(window)
    assert window.controller.session_id == "initial" and window.page.reviewing is None
    assert sources == ["initial"] and window.input.isEnabled()


@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_destroyed_page_detaches_running_preparation(window, monkeypatch, outcome):
    prepared = window.sessions.worker.prepare_replay(SessionToken(window.controller.session_id, window.controller.runtime_id), 1, 1).result(5)
    future = Future()
    future.set_running_or_notify_cancel()
    monkeypatch.setattr(window.sessions.worker, "prepare_replay", lambda *args: future)
    child = AssistantWindow(window.controller, sessions=window.sessions)
    receipt = child.page.receipt
    applied, destroyed = [], []
    child.page.activated.connect(lambda *args: applied.append(args))
    child.destroyed.connect(lambda: destroyed.append(True))
    assert child.page.opening and not receipt.pending[0].done()
    child.deleteLater()
    QApplication.instance().sendPostedEvents(None, QEvent.DeferredDelete)
    assert destroyed and receipt.pending is None and not future.cancelled()
    if outcome == "failure":
        future.set_exception(OSError("obsolete error"))
    else:
        future.set_result(prepared)
    heartbeat(window)
    assert not applied and receipt.pending is None


def test_destroyed_preview_ignores_a_queued_replay_batch(window, monkeypatch):
    target = window.sessions.worker.open_session("history").result(5)
    for number in range(40):
        record_task(target.journal, target.current, "turn-" + str(number))
    child = AssistantWindow(window.controller, sessions=window.sessions)
    settle(child)
    child.page.preview_session("history")
    child.page.receipt.pending[0].result(5)
    scheduled = []
    with monkeypatch.context() as patch:
        patch.setattr(QTimer, "singleShot", lambda delay, callback: scheduled.append(callback))
        child.page.receipt.poll()
    assert scheduled and child.page.opening and child.presentation.replay is not None
    # Retired bindings may be cyclically collected before Qt delivers DeferredDelete.
    gc.collect()
    child.deleteLater()
    QApplication.instance().sendPostedEvents(None, QEvent.DeferredDelete)
    for callback in scheduled:
        callback()


def test_failed_preparation_preserves_live_draft_and_allows_retry(window, monkeypatch):
    target = window.sessions.worker.open_session("history").result(5)
    original = target.journal.prepare_replay
    window.input.setPlainText("draft survives preparation error")

    def broken(_messages, *, after=None):
        raise OSError("storage unavailable")

    monkeypatch.setattr(target.journal, "prepare_replay", broken)
    window.page.activate_session("history")
    settle(window)
    assert window.controller.session_id == "initial" and window.input.isEnabled()
    assert window.input.toPlainText() == "draft survives preparation error"
    assert "storage unavailable" in notice_text(window)
    assert "storage unavailable" not in window.status.text()
    monkeypatch.setattr(target.journal, "prepare_replay", original)
    activate(window, "history")
    activate(window, "initial")
    assert window.input.toPlainText() == "draft survives preparation error"
