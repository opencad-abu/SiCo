"""Composed owners work without a window or a session controller host."""

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from threading import RLock
from types import SimpleNamespace
from unittest.mock import create_autospec

import pytest
from PyQt5.QtCore import QObject
from PyQt5.QtWidgets import QApplication

from sico.core.contracts import BoundContext
from sico.service.backend_commands import SessionBackendCommands
from sico.service.binding_commands import SessionBindings
from sico.service.execution_view import ExecutionView
from sico.service.published import freeze
from sico.service.thread_commands import SessionThreadCommands
from sico.storage.journal import BULK_KINDS, SessionJournal
from sico_ui.close_lifecycle import CloseLifecycle
from sico_ui.commands import CommandReceipts
from sico_ui.draft_book import DraftBook
from sico_ui.pending_commands import PendingCommands
from sico_ui.session_activation import NavigationState
from sico_ui.session_binding import SessionBinding
from sico_ui.session_presentation import SessionPresentation


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def journal():
    value = create_autospec(SessionJournal, instance=True, spec_set=True)
    value.events.return_value = []
    return value


def test_disposed_navigation_rejects_preparation_without_widget_signals(app):
    handle = SimpleNamespace(session_id="session", runtime_id="runtime")
    callbacks, closed, signals = [], [], []
    api = SimpleNamespace(view=lambda _: "view", handle=lambda h: h, owns=lambda _: True,
                          prepare_replay=lambda *_: Future(), replay_matches=lambda *_: True)
    receipt = SimpleNamespace(clear=lambda: None,
                              watch=lambda future, success, failure: callbacks.append(success))
    page = NavigationState(api, handle, receipt=receipt, closing=lambda: False)
    page.activated.connect(lambda *_: signals.append("activated"))
    page.invalidated.connect(lambda: signals.append("invalidated"))
    page.activate()
    page.dispose()
    callbacks[0](SimpleNamespace(stream=SimpleNamespace(close=lambda: closed.append(True))))
    assert closed == [True] and signals == ["invalidated"]


def test_preview_identity_is_visible_when_navigation_begins(app):
    handle = SimpleNamespace(session_id="live", runtime_id="runtime")
    observations = []
    api = SimpleNamespace(view=lambda _: "view", session=lambda _: None,
                          preview_session=lambda *_: Future())
    receipt = SimpleNamespace(clear=lambda: None, watch=lambda *_: None)
    page = NavigationState(api, handle, receipt=receipt, closing=lambda: False)
    page.began.connect(lambda: observations.append((page.opening, page.reviewing)))
    page.preview_session("history")
    assert observations == [(True, "history")] and page.session is handle


def test_duplicate_guard_is_atomic_and_receipt_settles_after_disposal(app):
    ledger, parent, future = PendingCommands(), QObject(), Future()
    with ThreadPoolExecutor(max_workers=4) as workers:
        assert sum(workers.map(ledger.begin, ["origin"] * 16)) == 1
    settled, displayed = [], []
    receipts = CommandReceipts(parent, capture=lambda: "scope", current=lambda _: True,
        closing=lambda: False, refresh=lambda: None, show_error=displayed.append)
    receipts.watch(future, success=displayed.append,
        settled=lambda result, error: (ledger.finish("origin"), settled.append((result, error))))
    receipts.dispose()
    future.set_result("accepted")
    receipts.poll()
    receipts.poll()
    assert not ledger and settled == [("accepted", None)] and not displayed


def test_command_failure_callback_owns_its_context_without_duplicate_notice(app):
    parent, label = QObject(), []
    receipts = CommandReceipts(parent, capture=lambda: "scope", current=lambda _: True,
        closing=lambda: False, refresh=lambda: None, show_error=label.append)
    def failed(exc):
        receipts.report("Draft kept: " + str(exc))
        return False
    receipts.rejected(ValueError("offline"), failure=failed)
    assert receipts.error == "Draft kept: offline"
    assert label == ["Draft kept: offline"]


def test_draft_queries_cannot_mutate_saved_media_and_late_acceptance_is_scoped():
    book = DraftBook()
    origin = SimpleNamespace(session_id="session", runtime_id="first")
    replacement = SimpleNamespace(session_id="session", runtime_id="second")
    draft = ("text", None, 0, 0, [{"type": "mention", "path": "/original"}], None)
    book.save(origin, 1, draft)
    draft[4][0]["path"] = "/escaped"
    queried = book.load(origin)
    queried[4].clear()
    assert book.load(origin)[4][0]["path"] == "/original"
    book.save(replacement, 1, ("replacement", None, 0, 0))
    book.accept(origin, 1)
    assert book.load(replacement)[0] == "replacement"
    assert not hasattr(book, "values")


def test_close_deadline_does_not_restart_and_late_guard_is_armed():
    now, armed = [10], []
    api = SimpleNamespace(desktop_busy=True, close_desktop=lambda: None)
    lifecycle = CloseLifecycle(api, clock=lambda: now[0])
    lifecycle.request()
    now[0] = 14
    lifecycle.request()
    lifecycle.attach_guard(SimpleNamespace(arm=lambda: armed.append(True)))
    assert lifecycle.deadline == 15 and not lifecycle.ready() and armed == [True]
    now[0] = 15
    assert lifecycle.ready() and lifecycle.forced


def test_presentation_keeps_current_display_default_and_history_semantics_distinct():
    current = BoundContext("i", "g", "current", {})
    default = replace(current, target_id="default")
    presentation = SessionPresentation(current)
    snapshot = freeze({"context": current.record(), "default_context": default.record(),
                       "binding_state": {"revision": 1}})
    presentation.update(snapshot)
    replay = presentation.preview()
    assert presentation.displayed_context == current
    assert presentation.submission_context == default and replay.read_only
    assert presentation.current_transcript("history") is replay
    assert presentation.current_transcript(None) is not replay


def test_service_commands_use_declared_observation_and_launch_dependencies(journal):
    from sico.core.backend_support import CODEX_SUPPORT

    context = BoundContext("i", "g", "target", {})
    view = ExecutionView("s", "r", context, True, 0, False, "", False, False,
                         freeze({"id": "task", "status": "executing", "steer_turn": "turn",
                                 "steer_thread": "thread"}), False, 1)
    calls, changes = [], []
    loop = SimpleNamespace(steering=SimpleNamespace(submit=lambda *args: calls.append(args)),
                           journal=journal, support=CODEX_SUPPORT)
    commands = SessionBackendCommands(loop, lock=RLock(), execution=lambda: view,
        check_input=lambda _: None, binding_state=lambda: {}, changed=lambda: changes.append(True))
    scope = commands.steering_scope()
    commands.steer("supplement", scope=scope, request_id="receipt")
    assert calls[0][1]["context"] == context.record()
    view = replace(view, cancelled=True)
    with pytest.raises(ValueError):
        commands.steer("stale", scope=scope, request_id="late")
    view = replace(view, busy=False, cancelled=False, task=freeze({}))
    loop.thread_ops = SimpleNamespace(available=lambda _: None)
    loop.state = object()
    commands = SessionThreadCommands(loop, lock=RLock(), execution=lambda: view,
        check_input=lambda _: None, binding_state=lambda: {}, interrupt=lambda: None,
        changed=lambda: None, launch=lambda *args: calls.append(args))
    commands.thread_operation({"kind": "compact"}, scope=commands.thread_scope(), request_id="compact")
    assert calls[-1][0] is loop.thread_ops and calls[-1][2] == "compact"
    journal.events.assert_called_once_with(exclude=BULK_KINDS)


def test_binding_reservation_does_not_wait_for_worker_io(journal):
    context = BoundContext("i", "g", "target", {})
    view = ExecutionView("s", "r", context, False, 0, False, "", False, False,
                         freeze({}), False, 1)
    lock = RLock()
    bindings = SessionBindings(journal, context, lock=lock,
        execution=lambda: view, changed=lambda: None, failed=lambda _: None,
        replace_current=lambda *_: None, pending_contexts=lambda: [])
    journal.events.assert_called_once_with(exclude=BULK_KINDS)
    with ThreadPoolExecutor(max_workers=1) as workers:
        with lock:
            reservation = workers.submit(bindings.reserve_submission, context).result(1)
    assert bindings.reserved(reservation) == context
    bindings.release_submission(reservation)
    assert bindings.reserved(reservation) is None


def test_event_adapter_closes_with_only_declared_dependencies(app):
    calls, now, parent = [], [10], QObject()
    lifecycle = CloseLifecycle(SimpleNamespace(desktop_busy=True, close_desktop=lambda: None),
                               clock=lambda: now[0])
    adapter = SessionBinding(parent, "session", api=SimpleNamespace(handle=lambda s: s),
        page=SimpleNamespace(opening=False), presentation=SimpleNamespace(state=None),
        lifecycle=lifecycle, receive=lambda _: None, update=lambda *_: None,
        caught_up=lambda: None, refresh_status=lambda: None, refresh_cursor=lambda: None,
        flush=lambda: None, finish_close=lambda: calls.append("closed"),
        stream_failed=lambda: None, released=lambda _: None)
    lifecycle.request()
    adapter.poll()
    assert not calls
    now[0] = 15
    adapter.poll()
    assert calls == ["closed"] and not adapter.timer.isActive()
