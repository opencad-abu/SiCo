"""Thread metadata persistence, deduplication and idle RPC ownership."""

from __future__ import annotations

import os
import queue
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from sico.codex.backend import CodexBackend
from sico.codex.rpc import AppServer, RpcError
from sico.core.contracts import BoundContext
from sico.core.tools import ToolRegistry
from sico.service.controller import SessionController
from sico.service.metadata import MetadataService
from sico.storage.history import session_catalog
from sico.storage.journal import SessionJournal
from sico.storage.names import session_names

CONTEXT = BoundContext("instance", "generation", "target", {"valid": True})
LABEL = "Codex 0.162.1 / test-model / native"


@pytest.fixture
def backend(tmp_path):
    with SessionJournal(tmp_path, "names") as journal:
        loop = CodexBackend(SimpleNamespace(label=LABEL), ToolRegistry(), journal, CONTEXT)
        from codex_thread_fixture import bind_thread
        bind_thread(loop, "thread-a")
        yield loop


def notification(value, thread_id="thread-a"):
    return {"method": "thread/name/updated",
            "params": {"threadId": thread_id, "threadName": value}}


def name_events(backend):
    return [event for event in backend.journal.events() if event["kind"] == "codex.thread.name"]


class MetadataRpc:
    def __init__(self, names, *, name=None, preview="Design a low-noise LDO"):
        self.names, self.name, self.preview = names, name, preview
        self.calls = []

    def request(self, method, params, timeout=30):
        self.calls.append((method, params))
        if method == "thread/read":
            return {"thread": {"id": "thread-a", "name": self.name, "preview": self.preview}}
        assert method == "thread/name/set"
        self.name = params["name"]
        self.names.notification(notification(self.name))
        return {}


def test_native_names_are_deduplicated_and_foreign_or_invalid_events_ignored(backend):
    names = backend.names
    names.capture({"id": "thread-a", "name": None, "preview": ""})
    assert not name_events(backend)
    for event in (notification("wrong", "child"), notification({"bad": True}),
                  {**notification("request"), "id": 1}, {"method": "new/unknown"}):
        names.notification(event)
    assert not name_events(backend)
    names.notification(notification("LDO review"))
    names.notification(notification("LDO review"))
    names.capture({"id": "thread-a", "name": "LDO review"})
    assert len(name_events(backend)) == 1
    assert session_names(backend.journal.events())["name"] == "LDO review"
    assert session_catalog(backend.journal.root)[0]["name"] == "LDO review"


def test_first_preview_names_once_and_manual_name_wins_after_more_turns(backend):
    rpc = MetadataRpc(backend.names)
    backend.names.after_turn(rpc)
    assert backend.names.metadata["name"] == "Design a low-noise LDO"
    assert backend.names.metadata["name_source"] == "codex.preview"
    assert len(name_events(backend)) == 1  # Notification and set reply share one event.
    backend.names.after_turn(rpc)
    assert len(name_events(backend)) == 1
    backend.names.set(rpc, "Manual review")
    backend.names.after_turn(rpc)
    assert backend.names.metadata["name"] == "Manual review"
    assert backend.names.metadata["name_source"] == "user"
    assert len(name_events(backend)) == 2
    assert [method for method, _ in rpc.calls].count("thread/name/set") == 2


def test_empty_preview_does_not_name_startup_and_long_preview_is_bounded(backend):
    rpc = MetadataRpc(backend.names, preview="")
    backend.names.after_turn(rpc)
    assert not name_events(backend)
    rpc.preview = "LDO\n" + "analysis " * 40
    backend.names.after_turn(rpc)
    assert len(backend.names.metadata["name"]) <= 60
    assert "\n" not in backend.names.metadata["name"]
    assert backend.names.metadata["name"].endswith("...")


@pytest.mark.parametrize("fields", [{"threadName": None}, {}])
def test_name_clear_falls_back_to_codex_preview(backend, fields):
    backend.names.capture({"id": "thread-a", "name": "Explicit", "preview": "First message"})
    controller = SessionController(backend)
    backend.names.notification({"method": "thread/name/updated", "params": {
        "threadId": "thread-a", **fields,
    }})
    assert controller.display_name == "First message"
    assert controller.label == LABEL


def test_metadata_failure_preserves_completed_task(backend):
    backend.state.task = {"id": "task", "status": "completed", "pending_calls": []}

    class FailedRpc:
        def request(self, *args, **kwargs):
            raise RpcError("unavailable")

    backend.names.after_turn(FailedRpc())
    assert backend.state.task["status"] == "completed"
    assert backend.journal.events()[-1]["kind"] == "codex.thread.name_sync_failed"


def test_foreign_metadata_cannot_trigger_name_set(backend):
    backend.names.capture({"id": "thread-a", "preview": "Existing preview"})

    class ForeignRpc:
        def request(self, method, params, timeout=30):
            assert method == "thread/read"
            return {"thread": {"id": "child", "name": None, "preview": "Wrong title"}}

    backend.names.after_turn(ForeignRpc())
    assert not name_events(backend)
    assert backend.names.metadata["title"] == "Existing preview"
    assert backend.journal.events()[-1]["kind"] == "codex.thread.name_sync_failed"


def test_turn_collects_name_and_ignores_unknown_notification_with_metadata_timeout(backend):
    backend._begin("Review circuit", CONTEXT, "input", "conversation")
    messages = deque([
        notification("Foreign title", "child"),
        notification("Review circuit"),
        {"method": "future/notification", "params": None},
        {"method": "item/completed", "params": {
            "threadId": "thread-a", "turnId": "turn-a",
            "item": {"type": "agentMessage", "id": "answer", "text": "Review complete."},
        }},
        {"method": "turn/completed", "params": {
            "threadId": "thread-a", "turnId": "turn-a",
            "turn": {"id": "turn-a", "status": "completed"},
        }},
    ])

    class TurnRpc:
        def next(self):
            return messages.popleft()

        def request(self, method, params, timeout=30):
            assert method == "thread/read"
            raise RpcError("metadata timeout")

    backend.runtime = SimpleNamespace(rpc=TurnRpc())
    events = list(backend._turn("turn-a", lambda: False))
    assert backend.state.task["status"] == "completed"
    assert events[-1]["kind"] == "task.completed"
    assert events[-2]["kind"] == "codex.thread.name_sync_failed"
    assert [event["payload"]["name"] for event in name_events(backend)] == ["Review circuit"]


def test_old_manual_journal_restores_display_name_and_preserves_provider(backend):
    backend._event("codex.thread.name", {"thread_id": "thread-a", "name": "Old manual"})
    backend._event("codex.thread.name", {"thread_id": "child", "name": "Wrong child"})
    restored = CodexBackend(backend.provider, backend.tools, backend.journal, CONTEXT)
    controller = SessionController(restored)
    assert controller.display_name == "Old manual"
    assert controller.label == LABEL
    assert restored.names.metadata["name_source"] == "user"


def test_resume_null_native_name_does_not_erase_manual_name(backend):
    backend._event("codex.thread.name", {
        "thread_id": "thread-a", "name": "Manual review", "source": "user",
    })
    backend.names.capture({"id": "thread-a", "name": None, "preview": "First message"})
    assert backend.names.metadata["name"] == "Manual review"
    assert session_names(backend.journal.events())["name"] == "Manual review"


def test_display_name_reads_last_durable_name_during_slow_write_and_reopen(backend, monkeypatch):
    backend.names._name("Saved name", "user")
    controller = SessionController(backend)
    entered, release = threading.Event(), threading.Event()
    original = os.fsync

    def slow_sync(fd):
        if fd == backend.journal._fd:
            entered.set()
            assert release.wait(5)
        return original(fd)

    monkeypatch.setattr(os, "fsync", slow_sync)
    with ThreadPoolExecutor(max_workers=2) as worker:
        write = worker.submit(backend.names._name, "New saved name", "user")
        try:
            assert entered.wait(5)
            read = worker.submit(lambda: controller.display_name)
            assert read.result(1) == "Saved name"
        finally:
            release.set()
        write.result(5)
    assert controller.display_name == "New saved name"
    snapshot = backend.journal.name_metadata
    snapshot["name"] = "Caller mutation"
    assert controller.display_name == "New saved name"
    backend.journal.close()
    with SessionJournal(backend.journal.root.parents[2], "names") as reopened:
        assert reopened.name_metadata["name"] == "New saved name"
        assert reopened.name_metadata["name_source"] == "user"


def test_idle_poll_rejects_unbound_input_and_keeps_other_events_in_order(backend):
    rpc = object.__new__(AppServer)
    rpc.notification_handler = backend.names.notification
    sent = []
    rpc.send = lambda message, **kwargs: sent.append(message)
    request = {"id": 2, "method": "item/tool/requestUserInput", "params": {}}
    turn = {"method": "turn/completed", "params": {"threadId": "thread-a"}}
    unknown = {"method": "future/notification", "params": {}}
    rpc.pending = deque([request, notification("First"), turn])
    rpc.messages = queue.Queue(maxsize=4096)
    rpc.messages.put(notification("Idle name"))
    rpc.messages.put(unknown)
    backend.runtime = SimpleNamespace(rpc=rpc)
    controller = SessionController(backend)
    controller._running = True
    controller.poll_metadata()
    assert controller.display_name == "names"
    controller._running = False
    controller.updates.read_updates()
    assert controller.display_name == "names"  # UI reads do not consume RPC messages.
    metadata = MetadataService(lambda: (controller,), interval=0.01)
    try:
        deadline = time.monotonic() + 5
        while controller.display_name != "Idle name" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert session_catalog(backend.journal.root)[0]["name"] == "Idle name"
    finally:
        metadata.close()
        assert metadata.wait(5)
    assert controller.display_name == "Idle name"
    assert list(rpc.pending) == [turn, unknown]
    assert len(sent) == 1 and sent[0]["id"] == 2 and sent[0]["error"]["code"] == -32602
    assert rpc.messages.empty()
    controller.poll_metadata()
    assert len(name_events(backend)) == 2


@pytest.mark.parametrize("command", ["rename", "delete"])
def test_idle_metadata_does_not_consume_rpc_during_commands(backend, monkeypatch, command):
    controller = SessionController(backend)
    entered, release = threading.Event(), threading.Event()
    polled = []

    def slow_command(*args):
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(backend, "set_thread_name" if command == "rename" else "delete_thread",
                        slow_command)
    monkeypatch.setattr(backend, "poll_thread_names", lambda: polled.append(True))
    args = ("Review name",) if command == "rename" else ()
    with ThreadPoolExecutor(max_workers=2) as worker:
        operation = worker.submit(getattr(controller, command), *args)
        try:
            assert entered.wait(5)
            worker.submit(controller.poll_metadata).result(1)
            assert not polled
        finally:
            release.set()
        operation.result(5)


def test_busy_controller_does_not_block_other_idle_metadata_or_shutdown(backend):
    controller = SessionController(backend)
    observed = threading.Event()
    other = SimpleNamespace(poll_metadata=observed.set)
    with controller._lock:
        metadata = MetadataService(lambda: (controller, other), interval=0.01)
        try:
            assert observed.wait(1)
        finally:
            metadata.close()
            assert metadata.wait(1)


@pytest.mark.parametrize("source", ["pending", "messages", "request"])
def test_failed_notification_is_retained_for_retry_in_original_order(backend, source):
    rpc = object.__new__(AppServer)
    request = {"id": 20, "method": "item/tool/requestUserInput", "params": {}}
    update = notification("Retry after storage recovers")
    turn = {"method": "turn/completed", "params": {"threadId": "thread-a"}}
    rpc.pending, rpc.messages = deque([request]), queue.Queue()

    def failed_write(_event):
        raise OSError("journal unavailable")

    rpc.notification_handler = failed_write
    if source == "pending":
        rpc.pending.extend([update, turn])
    elif source == "messages":
        rpc.messages.put(update)
        rpc.messages.put(turn)
    else:
        rpc.counter = 0
        rpc.cancelled = None
        rpc.stopped, rpc._request_lock = threading.Event(), threading.Lock()

        def send(_request, _deadline, _cancelled):
            rpc.messages.put(update)
            rpc.messages.put(turn)
            return 1

        rpc._send = send
    with pytest.raises(OSError):
        if source == "request":
            rpc.request("thread/read", {"threadId": "thread-a"})
        else:
            rpc.poll_notifications()
    assert list(rpc.pending) + list(rpc.messages.queue) == [request, update, turn]
    rpc.notification_handler = backend.names.notification
    rpc.poll_notifications()
    assert [rpc.next(), rpc.next()] == [request, turn]
    assert backend.names.metadata["name"] == "Retry after storage recovers"


def test_idle_metadata_io_error_is_contained(backend, monkeypatch):
    def failed_poll():
        raise OSError("unavailable journal")

    monkeypatch.setattr(backend, "poll_thread_names", failed_poll)
    controller = SessionController(backend)
    controller.poll_metadata()
    assert controller.updates.read_updates()["snapshot"]["fault"] == ""


@pytest.mark.parametrize("name", ["", " ", None, "x" * 121, "bad\0name"])
def test_invalid_manual_name_never_sends_rpc(backend, name):
    rpc = MetadataRpc(backend.names)
    with pytest.raises(ValueError):
        backend.names.set(rpc, name)
    assert not rpc.calls
