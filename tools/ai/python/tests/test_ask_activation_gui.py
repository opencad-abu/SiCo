"""Ask activation completion and failures belong only to the selected page."""

from concurrent.futures import Future
from types import SimpleNamespace

import pytest
from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QApplication
from sico_ui.ask_activation import AskActivation


@pytest.fixture
def qapp():
    return QApplication.instance()


class Page(QObject):
    activated = pyqtSignal(object, object)
    invalidated = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, mode):
        super().__init__()
        self.session = SimpleNamespace(session_id="initial")
        self.opening = False
        self.reviewing = None
        self.activation = 0
        self.mode = mode

    def activate_session(self, session_id):
        self.activation += 1
        self.invalidated.emit()
        self.opening = True
        if self.mode == "sync":
            self.complete(session_id)
        elif self.mode == "fail":
            self.opening = False
            self.failed.emit("read failed")

    def complete(self, session_id):
        previous = self.session
        self.session = SimpleNamespace(session_id=session_id)
        self.opening = False
        self.activated.emit(previous, None)


def activation(mode="sync"):
    page, calls, notices, receipts = Page(mode), [], [], []

    def ensure(session):
        calls.append(("ensure", session.session_id))
        return Future()

    window = QObject()
    window.page = page
    window.restore = lambda: calls.append("restore")
    window.focus_input = lambda: calls.append("focus")
    window.api = SimpleNamespace(control=SimpleNamespace(ensure=ensure))
    window.commands = SimpleNamespace(watch=lambda future, **kwargs: receipts.append(kwargs))
    window.session_notices = SimpleNamespace(set=lambda *args, **_kwargs: notices.append(args))
    return AskActivation(window), page, calls, notices, receipts


def assert_detached(page):
    assert all(page.receivers(signal) == 0 for signal in
               (page.activated, page.failed, page.invalidated))


def test_synchronous_activation_requests_control_once_and_focus_never_navigates():
    ask, page, calls, notices, receipts = activation()
    ask.activate("accepted")
    assert calls == ["restore", ("ensure", "accepted"), "focus"]
    assert not notices and len(receipts) == 1
    assert page.activation == 1 and page.session.session_id == "accepted"
    assert_detached(page)


def test_new_ask_detaches_previous_callbacks_before_failure_or_completion():
    ask, page, calls, notices, receipts = activation("pending")
    ask.activate("old")
    ask.activate("new")
    assert page.receivers(page.activated) == 1
    page.complete("new")
    page.failed.emit("old replay failed later")
    assert calls == ["restore", "focus", "restore", "focus", ("ensure", "new")]
    assert not notices and len(receipts) == 1
    assert_detached(page)


@pytest.mark.parametrize("cancel", ["close", "navigate"])
def test_close_or_user_navigation_detaches_pending_ask(cancel):
    ask, page, calls, notices, receipts = activation("pending")
    ask.activate("abandoned")
    if cancel == "close":
        ask.close()
    else:
        page.activation += 1
        page.reviewing = "history"
        page.invalidated.emit()
    assert_detached(page)
    page.complete("abandoned")
    page.failed.emit("abandoned replay failed")
    assert calls == ["restore", "focus"] and not notices and not receipts


def test_failure_is_visible_and_detaches_without_acquiring_control():
    ask, page, calls, notices, receipts = activation("fail")
    ask.activate("missing")
    assert calls == ["restore", "focus"] and not receipts
    assert notices == [("attach", "会话未取得控制", "read failed")]
    assert_detached(page)


@pytest.mark.parametrize("change", ["none", "navigate", "new_ask", "close"])
def test_control_failure_is_scoped_to_the_activation_that_requested_it(change):
    ask, page, _calls, notices, receipts = activation()
    ask.activate("first")
    failure = receipts[0]["failure"]
    if change == "navigate":
        page.activation += 1
        page.reviewing = "history"
    elif change == "new_ask":
        ask.activate("second")
    elif change == "close":
        ask.close()
    assert failure(RuntimeError("control denied")) is (None if change == "none" else False)
    assert bool(notices) == (change == "none")


def wake_activation(mode="sync"):
    ask, page, calls, notices, watches = activation(mode)
    queries = []

    def query(session_id, input_id):
        queries.append((session_id, input_id))
        return Future()

    ask.window.api.ask_head = query
    ask.wake.receipt = SimpleNamespace(
        watch=lambda future, success, failure: watches.append(
            dict(success=success, failure=failure)),
        clear=lambda: None,
    )
    return ask, page, calls, notices, watches, queries


def receipt(input_id="first", sequence=1, *, head="first", execution="executing",
            recovery=False, session="accepted"):
    return SimpleNamespace(
        input_id=input_id, ask_sequence=sequence, outcome="accepted",
        execution=execution, recovery_required=recovery,
        address=SimpleNamespace(session=SimpleNamespace(session_id=session)),
        queue_head=None if head is None else dict(
            input_id=head, ask_sequence=sequence if head == input_id else 1))


def test_late_wake_checks_live_head_before_navigation_or_control(qapp):
    ask, page, calls, notices, watches, queries = wake_activation()
    ask.activate("accepted", "second", 2)
    ask.activate("accepted", "first", 1)
    assert queries == [("accepted", "second")] and not calls
    watches.pop(0)["success"](receipt("second", 2))
    qapp.processEvents()
    assert queries[-1] == ("accepted", "first") and not calls
    watches.pop(0)["success"](receipt())
    qapp.processEvents()
    assert page.session.session_id == "accepted" and page.activation == 1
    assert calls == ["restore", ("ensure", "accepted"), "focus"]
    assert not notices and not ask.wake.busy and len(watches) == 1
    ask.close()


@pytest.mark.parametrize("change", ["navigate", "close", "query_failure"])
def test_wake_query_failure_or_changed_page_never_acquires_control(change, qapp):
    ask, page, calls, notices, watches, _queries = wake_activation()
    ask.activate("accepted", "first", 1)
    if change == "navigate":
        page.activation += 1
    elif change == "close":
        ask.close()
    if change == "query_failure":
        watches[0]["failure"](RuntimeError("head unavailable"))
    else:
        watches[0]["success"](receipt())
    qapp.processEvents()
    assert not calls and not ask.wake.busy
    assert bool(notices) == (change == "query_failure")
    ask.close()


@pytest.mark.parametrize("row", [
    None, receipt(recovery=True), receipt(head=None), receipt(sequence=2),
    receipt(session="different"), receipt("other", 1, head="other"),
    receipt(head=None, execution="queued"),
])
def test_stale_or_unconfirmed_wake_is_ignored(row, qapp):
    ask, _page, calls, notices, watches, _queries = wake_activation()
    ask.activate("accepted", "first", 1)
    watches[0]["success"](row)
    qapp.processEvents()
    assert not calls and not notices and not ask.wake.busy
    ask.close()


def test_completed_idle_receipt_may_show_results_and_other_session_is_independent(qapp):
    ask, page, calls, _notices, watches, queries = wake_activation()
    ask.activate("accepted", "first", 1)
    ask.activate("other", "third", 3)
    watches.pop(0)["success"](receipt(head=None, execution="completed"))
    qapp.processEvents()
    assert queries == [("accepted", "first"), ("other", "third")]
    watches.pop()["success"](receipt("third", 3, head="third", session="other"))
    qapp.processEvents()
    assert page.session.session_id == "other" and page.activation == 2
    assert [item for item in calls if isinstance(item, tuple)] == [
        ("ensure", "accepted"), ("ensure", "other")]
    assert not ask.wake.busy
    ask.close()
