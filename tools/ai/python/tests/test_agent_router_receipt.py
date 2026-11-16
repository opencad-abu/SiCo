"""Receipt reads and display preparation preserve their exact source off Qt."""

from sico.service.frontend_session import SessionToken

import json
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest
from agent_sessions_harness import DesktopSessions

import sico.service.router_commands as controller_module
from sico.core.contracts import BoundContext, NeedsReconcile
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.providers.mock import ScriptedProvider
from sico.service.controller import SessionController
from sico.service.router_receipt import (
    MAX_RECEIPT_TEXT,
    latest_router_request,
    router_receipt_view,
)
from sico.storage.journal import SessionJournal

REQUEST_ID = "a" * 32
CONTEXT = BoundContext("instance", "generation", "target", {"window": 1})


def receipt_record(**changes):
    return dict({"instance_id": "instance", "generation": "generation", "target_id": "target",
                 "request_id": REQUEST_ID, "session_id": "receipt", "router_id": "router",
                 "state": "completed", "reply": {"label": "current design"},
                 "automatic_resume_allowed": False}, **changes)


def request_events(context=CONTEXT, **changes):
    request = dict(request_id=REQUEST_ID, target_id=context.target_id, session_id="receipt",
                   task_id="task", router_id="router", state="completed")
    request.update(changes)
    return [
        {"session_id": "receipt", "task_id": "task", "kind": "task.started",
         "payload": {"context": context.record()}},
        {"session_id": "receipt", "task_id": "task", "kind": "router.status", "payload": request},
    ]


@pytest.fixture
def sessions(tmp_path):
    with SessionJournal(tmp_path, "receipt") as journal:
        loop = PythonBackend(ScriptedProvider([]), ToolRegistry(), journal, CONTEXT)
        loop._begin("Read current design", None, None)
        loop._event("task.started", {"text": "Read current design", "context": CONTEXT.record()})
        loop._event("router.status", dict(request_events()[-1]["payload"],
                                          task_id=loop.state.task["id"]))
        loop._finish("completed")
        controller = SessionController(loop)
        owned = DesktopSessions(controller)
        try:
            yield owned
        finally:
            owned.close()
            assert owned.wait(5)


def test_request_selection_uses_task_source_and_skips_other_generations():
    events = request_events()
    for context in (replace(CONTEXT, generation="old"), replace(CONTEXT, instance_id="other"),
                    replace(CONTEXT, target_id="elsewhere")):
        events.extend(request_events(context, request_id="b" * 32))
    assert latest_router_request(events, CONTEXT, "receipt")["request_id"] == REQUEST_ID


@pytest.mark.parametrize("changes", [
    {"session_id": "other"}, {"task_id": "other"}, {"instance_id": "other"},
    {"generation": "old"}, {"target_id": "other"}, {"request_id": "input"},
    {"request_id": 11111111111111111111111111111111},
])
def test_mismatched_or_invalid_request_is_not_selected(changes):
    with pytest.raises(ValueError):
        latest_router_request(request_events(**changes), CONTEXT, "receipt")


def test_request_without_context_or_owned_event_is_not_selected():
    events = request_events()[1:]
    with pytest.raises(ValueError):
        latest_router_request(events, CONTEXT, "receipt")
    events = request_events(instance_id="instance", generation="generation")
    events[1]["session_id"] = "other"
    with pytest.raises(ValueError):
        latest_router_request(events, CONTEXT, "receipt")


@pytest.mark.parametrize("archived", [False, True])
def test_worker_prepares_detached_text_and_preserves_raw_receipt(sessions, tmp_path, archived):
    controller = sessions.controllers["receipt"]
    owner, calls = threading.get_ident(), []
    receipt = receipt_record()

    def read(context, request_id, *, session_id):
        assert threading.get_ident() != owner
        assert (context, request_id, session_id) == (CONTEXT, REQUEST_ID, "receipt")
        calls.append("read")
        if archived:
            raise NeedsReconcile("bridge disconnected")
        return receipt

    def archive(launch_dir, context, request_id, *, session_id):
        assert threading.get_ident() != owner
        assert launch_dir == tmp_path
        assert (context, request_id, session_id) == (CONTEXT, REQUEST_ID, "receipt")
        calls.append("archive")
        return receipt

    controller.bindings.attach_targets(SimpleNamespace(request_receipt=read, archived_receipt=archive))
    view = sessions.worker.command(SessionToken(controller.session_id, controller.runtime_id), "router_receipt", context=CONTEXT,
                                   formatted=True).result(5)
    assert (view.session_id, view.runtime_id, view.context) == (
        controller.session_id, controller.runtime_id, CONTEXT,
    )
    assert view.context is not CONTEXT and view.request_id == REQUEST_ID
    assert json.loads(view.text) == receipt and not hasattr(view, "reply")
    receipt["reply"]["label"] = "changed later"
    assert json.loads(view.text)["reply"]["label"] == "current design"
    raw = sessions.worker.command(SessionToken(controller.session_id, controller.runtime_id), "router_receipt", context=CONTEXT).result(5)
    assert raw == receipt
    assert calls == (["read", "archive"] * 2 if archived else ["read"] * 2)


@pytest.mark.parametrize("field", ["session_id", "request_id", "instance_id", "generation",
                                  "target_id", "router_id"])
def test_worker_refuses_misowned_receipt_before_formatting(sessions, monkeypatch, field):
    controller = sessions.controllers["receipt"]
    controller.bindings.attach_targets(SimpleNamespace(
        request_receipt=lambda *args, **kwargs: receipt_record(**{field: "other"}),
    ))
    formatted = []
    monkeypatch.setattr(controller_module, "router_receipt_view",
                        lambda *args: formatted.append(args))
    with pytest.raises(NeedsReconcile, match="不匹配"):
        sessions.worker.command(SessionToken(controller.session_id, controller.runtime_id), "router_receipt", context=CONTEXT,
                                formatted=True).result(5)
    assert not formatted


@pytest.mark.parametrize("stage, change", [("queued", "target"), ("read", "target"),
                                          ("format", "target"), ("read", "broker"),
                                          ("read", "shutdown")])
def test_worker_rechecks_source_before_and_after_slow_work(sessions, monkeypatch, stage, change):
    controller = sessions.controllers["receipt"]
    entered, release, calls = threading.Event(), threading.Event(), []
    format_view = controller_module.router_receipt_view

    def hold():
        entered.set()
        assert release.wait(5)

    def read(*args, **kwargs):
        calls.append("read")
        if stage == "read":
            hold()
        return receipt_record()

    def formatting(*args):
        if stage == "format":
            hold()
        return format_view(*args)

    controller.bindings.attach_targets(SimpleNamespace(request_receipt=read))
    monkeypatch.setattr(controller_module, "router_receipt_view", formatting)
    if stage == "queued":
        sessions.worker._request(hold)
    future = sessions.worker.command(SessionToken(controller.session_id, controller.runtime_id), "router_receipt", context=CONTEXT, formatted=True)
    try:
        assert entered.wait(2)
        assert controller.updates.read_updates(blocking=False)["snapshot"] is not None
        with controller._lock:
            if change == "target":
                controller.current = replace(CONTEXT, target_id="new-target")
            elif change == "broker":
                controller.bindings.broker = SimpleNamespace(request_receipt=read)
            else:
                controller.request_close()
    finally:
        release.set()
    with pytest.raises(ValueError, match="来源已变化"):
        future.result(5)
    assert calls == ([] if stage == "queued" else ["read"])


def test_formatting_caps_utf8_text_and_does_not_truncate_raw_evidence():
    receipt = receipt_record(reply={"text": "\u8bbe\u8ba1" * MAX_RECEIPT_TEXT})
    view = router_receipt_view(receipt, CONTEXT, "receipt", "runtime")
    assert len(view.text.encode("utf-8")) <= MAX_RECEIPT_TEXT
    assert "已截断显示" in view.text and "\ufffd" not in view.text
    assert len(receipt["reply"]["text"]) == 2 * MAX_RECEIPT_TEXT
