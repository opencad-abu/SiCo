"""Frontend handles, independent queues, and slow diagnostic handler boundaries."""

import logging
import threading
from concurrent.futures import Future
from dataclasses import FrozenInstanceError, replace

import pytest
from agent_gui_wait import settle, wait_until
from agent_worker_fixtures import heartbeat
from agent_worker_fixtures import window as window
from PyQt5.QtCore import QEvent
from PyQt5.QtWidgets import QApplication

from sico.service.diagnostics import diagnostics
from sico_ui.quick_input import QuickInput
from sico_ui.receipts import DataReceipt
from agent_window_fixture import create_window as AssistantWindow


def test_handle_configuration_is_immutable_and_does_not_publish_backend_objects(window):
    handle, api = window.page.session, window.api
    view = api.view(handle)
    assert not hasattr(handle, "journal") and not hasattr(handle, "loop")
    with pytest.raises(FrozenInstanceError):
        handle.runtime_id = "other"
    with pytest.raises(TypeError):
        view.context.snapshot["changed"] = True
    assert api.catalog_snapshot()["version"] >= 0
    assert api.layout_path.name == "workspace.ini"
    assert api.session(handle.session_id) == handle


@pytest.mark.parametrize("name", ["submit", "rename", "delete"])
def test_queue_does_not_retarget_a_command_after_runtime_replacement(window, monkeypatch, name):
    api, owner = window.api, window.controller
    entered, release = threading.Event(), threading.Event()
    calls = []

    def hold():
        entered.set()
        assert release.wait(5)

    blocker = window.sessions.worker._request(hold)
    assert entered.wait(5)
    monkeypatch.setattr(owner, name, lambda *a, **kw: calls.append(name))
    if name == "submit":
        future = api.command(window.page.session, "submit", "original", context=owner.current)
        assert owner.bindings._submissions
    elif name == "rename":
        future = api.rename_session(owner.session_id, "new name")
    else:
        future = api.delete_session(owner.session_id)
    original_runtime = owner.runtime_id
    owner.runtime_id = "replacement"
    try:
        heartbeat(window)
        assert not release.is_set()
        release.set()
        blocker.result(5)
        with pytest.raises(ValueError, match="runtime"):
            future.result(5)
        wait_until(lambda: not owner.bindings._submissions)
        assert not calls
    finally:
        release.set()
        owner.runtime_id = original_runtime


def test_durable_success_before_switch_accounts_for_draft_before_display_callback(window,
                                                                                  monkeypatch):
    future = Future()
    monkeypatch.setattr(window.api, "command", lambda *a, **kw: future)
    window.input.setPlainText("accepted draft")
    window.actions.submit()
    window.commands.timer.stop()
    future.set_result("accepted")
    window.page.activate_session("history")
    wait_until(lambda: not window.page.opening)
    assert window.drafts.load(window.api.session("initial"))[0] == ""
    window.page.activate_session("initial")
    wait_until(lambda: not window.page.opening)
    assert window.input.toPlainText() == ""


def test_bookkeeping_can_finish_after_qt_has_destroyed_quick_input(window, monkeypatch):
    child = AssistantWindow(window.controller, sessions=window.sessions)
    settle(child)
    attach = Future()
    attach.set_result(None)
    monkeypatch.setattr(child.api, "command", lambda *a, **kw: attach)
    notices = []
    child.quick_input = QuickInput(child, lambda kind, **kw: notices.append((kind, kw)))
    future = Future()
    monkeypatch.setattr(child.api, "accept_quick", lambda *a: future)
    child.quick_input.accept({"id": "late_quick"})
    child.deleteLater()
    QApplication.instance().sendPostedEvents(None, QEvent.DeferredDelete)
    future.set_result(object())
    assert notices == [("accepted", {"id": "late_quick"})]


def test_slow_logging_handler_cannot_hold_qt_receipts_or_heartbeat(window):
    entered, release = threading.Event(), threading.Event()
    thread_ids = []
    owner = threading.get_ident()

    class SlowHandler(logging.Handler):
        def emit(self, _record):
            thread_ids.append(threading.get_ident())
            entered.set()
            assert release.wait(5)

    logger = logging.getLogger("sico_ui.receipts")
    handler = SlowHandler()
    logger.addHandler(handler)
    receipt = DataReceipt(window)
    future = Future()
    future.set_exception(ValueError("query failed"))

    def broken(_error):
        raise RuntimeError("callback failed")

    try:
        receipt.watch(future, lambda _: None, broken)
        receipt.poll()
        assert entered.wait(2)
        assert not receipt.pending
        heartbeat(window)
        assert not release.is_set() and len(thread_ids) == 1
        assert thread_ids[0] != owner
        dropped = diagnostics.dropped
        for _ in range(80):
            diagnostics.submit("fb04.overload", "bounded diagnostic")
        assert diagnostics.queue.qsize() <= 64 and diagnostics.dropped > dropped
    finally:
        release.set()
        logger.removeHandler(handler)
        wait_until(lambda: diagnostics.queue.unfinished_tasks == 0)


def test_target_scope_change_drops_late_picker_query(window, monkeypatch):
    from sico_ui.target_picker import TargetPicker

    future, closed, selected = Future(), [], []

    class Source:
        can_create = False

        def libraries(self, _readonly):
            return future

        def close(self):
            closed.append(True)

    window.actions.pick_design_target(selected.append, Source())
    picker = window.actions._target_picker
    assert isinstance(picker, TargetPicker)
    future.set_running_or_notify_cancel()
    window.presentation.displayed_context = replace(window.presentation.displayed_context, target_id="other")
    future.set_result([{"name": "old", "read_only": False}])
    picker.receipt.poll()
    assert selected == [None]
    assert not picker.library.count() and closed
