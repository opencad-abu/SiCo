"""Command completion belongs to its runtime; only matching pages may present it."""

from concurrent.futures import Future
from dataclasses import replace

import pytest
from agent_gui_wait import settle, wait_until
from agent_window_fixture import create_window as AssistantWindow
from agent_worker_fixtures import window as window
from info_app_fixtures import dismiss_notice, notice_text
from PyQt5.QtCore import QEvent
from PyQt5.QtWidgets import QApplication


def finish(future, result):
    if result == "success":
        future.set_result("accepted")
    elif result == "failure":
        future.set_exception(ValueError("old refusal"))
    else:
        assert future.cancel()


def change_page(window, transition):
    if transition in {"other", "roundtrip"}:
        window.page.activate_session("history")
        wait_until(lambda: not window.page.opening)
        if transition == "roundtrip":
            window.page.activate_session("initial")
            wait_until(lambda: not window.page.opening)
    elif transition == "preview":
        window.page.preview_session("history")
        wait_until(lambda: not window.page.opening)
    elif transition == "runtime":
        window.controller.runtime_id = "replacement"
    elif transition == "target":
        window.presentation.displayed_context = replace(window.presentation.displayed_context, target_id="new-target")
    elif transition == "generation":
        window.controller.current = replace(window.controller.current, generation="new-generation")
    elif transition == "close":
        window.lifecycle.closing = True
    window.binding.timer.stop()
    window.navigation.timer.stop()


@pytest.mark.parametrize("result", ["success", "failure", "cancel"])
@pytest.mark.parametrize("transition", ["other", "roundtrip", "preview", "runtime", "target",
                                        "generation", "close"])
def test_submit_late_receipt_preserves_current_page_and_settles_original(
    window, monkeypatch, transition, result,
):
    old = window.controller
    runtime, context = old.runtime_id, old.current
    future = Future()
    monkeypatch.setattr(window.api, "command", lambda *a, **kw: future)
    window.input.setPlainText("original draft")
    window.actions.submit()
    assert window.submissions.snapshot() == {("initial", runtime)}
    change_page(window, transition)
    window.input.setPlainText("current draft")
    window.status.setText("current status")
    window.commands.error = "current error"
    try:
        finish(future, result)
        window.commands.poll()
        assert not window.submissions
        assert window.input.toPlainText() == "current draft"
        assert window.status.text() == "current status"
        assert window.commands.error == "current error"
        assert not notice_text(window)
        if transition == "other":
            expected = "" if result == "success" else "original draft"
            assert window.drafts.load(old)[0] == expected
    finally:
        old.runtime_id, old.current = runtime, context


@pytest.mark.parametrize("operation", ["initialize_context", "resume", "acknowledge_interrupted",
                                       "answer_audit", "rename", "delete", "cancel"])
@pytest.mark.parametrize("result", ["success", "failure", "cancel"])
def test_all_command_entry_points_automatically_scope_display(window, monkeypatch, operation,
                                                              result):
    future, shown = Future(), []
    monkeypatch.setattr(window.api, "command", lambda *a, **kw: future)
    monkeypatch.setattr(window.api, "rename_session", lambda *a: future)
    monkeypatch.setattr(window.api, "delete_session", lambda *a: future)
    callbacks = dict(success=lambda value: shown.append(value),
                     failure=lambda error: shown.append(error))
    if operation == "rename":
        window.actions.rename_session("history", "renamed", **callbacks)
    elif operation == "delete":
        window.actions.delete_session("history", **callbacks)
    else:
        window.actions.run_command(operation, **callbacks)
    window.page.activation += 1
    window.status.setText("new page")
    finish(future, result)
    window.commands.poll()
    assert not shown and window.status.text() == "new page"
    assert not notice_text(window)


@pytest.mark.parametrize("result", ["failure", "cancel"])
def test_current_failure_is_visible_and_draft_is_retryable(window, monkeypatch, result):
    future = Future()
    monkeypatch.setattr(window.api, "command", lambda *a, **kw: future)
    window.input.setPlainText("keep draft")
    window.actions.submit()
    finish(future, result)
    window.commands.poll()
    assert not window.submissions and window.send.isEnabled()
    assert window.input.toPlainText() == "keep draft"
    assert ("old refusal" if result == "failure" else "已取消") in notice_text(window)
    # The operation-specific handler owns the error; no second generic popup follows.
    dismiss_notice(window)
    QApplication.processEvents()
    assert not notice_text(window)


def test_stop_failure_is_inline_and_does_not_open_a_notice(window, monkeypatch):
    future = Future()
    monkeypatch.setattr(window.api, "command", lambda *args, **kwargs: future)
    state = dict(window.presentation.state)
    state["task"] = {**state["task"], "id": "task-1"}
    window.presentation.state = state

    window.renderer.cancel_task()
    future.set_exception(ValueError("原任务已变化，操作未执行"))
    window.commands.poll()

    assert not notice_text(window)
    assert window.status.text() == "停止请求未生效：原任务已变化，操作未执行"


def test_retyped_identical_draft_is_not_cleared_by_prior_submission(window, monkeypatch):
    future = Future()
    monkeypatch.setattr(window.api, "command", lambda *a, **kw: future)
    window.input.setPlainText("same text")
    window.actions.submit()
    window.input.clear()
    window.input.setPlainText("same text")
    future.set_result("accepted")
    window.commands.poll()
    assert window.input.toPlainText() == "same text"


def test_immediate_queue_rejection_cleans_pending_and_preserves_draft(window, monkeypatch):
    def reject(*_args, **_kwargs):
        raise ValueError("Desktop command queue is full")

    monkeypatch.setattr(window.api, "command", reject)
    window.input.setPlainText("retry this draft")
    window.actions.submit()
    assert not window.submissions and window.send.isEnabled()
    assert window.input.toPlainText() == "retry this draft"
    assert "queue is full" in notice_text(window)


def test_old_receipt_does_not_clear_new_runtime_submission(window, monkeypatch):
    original, replacement = Future(), Future()
    futures = iter((original, replacement))
    monkeypatch.setattr(window.api, "command", lambda *a, **kw: next(futures))
    owner, handle = window.controller, window.page.session
    window.input.setPlainText("original")
    window.actions.submit()
    try:
        owner.runtime_id = "replacement"
        window.page.session = window.api.session(owner.session_id)
        window.page.activation += 1
        window.input.setPlainText("replacement draft")
        window.actions.submit()
        original.set_result("accepted")
        window.commands.poll()
        assert window.submissions.snapshot() == {(handle.session_id, "replacement")}
        assert window.input.toPlainText() == "replacement draft"
        replacement.set_exception(ValueError("new refusal"))
        window.commands.poll()
        assert not window.submissions and "new refusal" in notice_text(window)
    finally:
        owner.runtime_id = handle.runtime_id
        window.page.session = handle


def test_success_callback_exception_is_visible_without_skipping_bookkeeping(window, monkeypatch):
    future, completed = Future(), []
    monkeypatch.setattr(window.api, "command", lambda *a, **kw: future)

    def broken(_result):
        raise RuntimeError("display failed")

    window.actions.run_command("initialize_context", success=broken,
                       settled=lambda value, error: completed.append((value, error)))
    future.set_result(42)
    window.commands.poll()
    assert completed == [(42, None)]
    assert "display failed" in notice_text(window)


def test_failure_callback_exception_does_not_skip_cleanup_or_other_receipts(window, monkeypatch):
    failed, following = Future(), Future()
    futures, completed, shown = iter((failed, following)), [], []
    monkeypatch.setattr(window.api, "command", lambda *a, **kw: next(futures))

    def broken(_error):
        raise RuntimeError("error display failed")

    window.actions.run_command("initialize_context", failure=broken,
                       settled=lambda value, error: completed.append((value, str(error))))
    window.actions.run_command("resume", success=shown.append)
    failed.set_exception(ValueError("refused"))
    following.set_result("next receipt")
    window.commands.poll()
    assert completed == [(None, "refused")] and shown == ["next receipt"]
    assert not window.commands.pending and "error display failed" in notice_text(window)


def test_audit_pending_set_is_cleaned_after_page_change(window, monkeypatch):
    future = Future()
    monkeypatch.setattr(window.api, "command", lambda *a, **kw: future)
    window.actions.answer_audit("audit", {})
    assert window.actions._answering
    window.page.activation += 1
    window.status.setText("new page")
    future.set_result(True)
    window.commands.poll()
    assert not window.actions._answering and window.status.text() == "new page"


def test_binding_completion_cannot_release_a_new_page_command(window, monkeypatch):
    original, replacement = Future(), Future()
    futures = iter((original, replacement))
    monkeypatch.setattr(window.api, "command", lambda *a, **kw: next(futures))
    panel = window.binding_panel
    panel.command("configure_bindings")
    window.page.activation += 1
    panel.update_state(window.presentation.state)
    panel.command("configure_bindings")
    original.set_result(True)
    window.commands.poll()
    assert panel.pending_command
    replacement.set_exception(ValueError("current binding failure"))
    window.commands.poll()
    assert not panel.pending_command
    assert "current binding failure" in panel.notice.text()


def test_runtime_retiring_during_receipt_validation_discards_only_display(window, monkeypatch):
    future, completed, shown = Future(), [], []
    monkeypatch.setattr(window.api, "command", lambda *a, **kw: future)
    window.actions.run_command("initialize_context", success=shown.append,
                       settled=lambda *_: completed.append(True))

    def retired(_handle):
        raise ValueError("Session runtime is no longer current")

    monkeypatch.setattr(window.api, "connection", retired)
    future.set_result(True)
    window.commands.poll()
    assert completed == [True] and not shown and not window.commands.pending


def test_destroyed_qobject_still_settles_submission(window, monkeypatch):
    child = AssistantWindow(window.controller, sessions=window.sessions)
    settle(child)
    future = Future()
    monkeypatch.setattr(child.api, "command", lambda *a, **kw: future)
    child.input.setPlainText("destroyed draft")
    child.actions.submit()
    pending = child.submissions
    binding = child.commands
    child.deleteLater()
    QApplication.instance().sendPostedEvents(None, QEvent.DeferredDelete)
    assert binding.disposed and not binding.pending
    future.set_result("accepted")
    assert not pending
