"""Deterministic journal write/fsync gates and valid replay histories."""

import os
import threading
from concurrent.futures import Future
from contextlib import contextmanager
from copy import deepcopy

from sico.core.contracts import RunState


class Gate:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.expired = False

    def pause(self):
        self.entered.set()
        if not self.release.wait(5):
            self.expired = True
            raise AssertionError("Dependency was not released by the responsive Qt caller")


def background(operation):
    future = Future()

    def run():
        try:
            future.set_result(operation())
        except BaseException as exc:
            future.set_exception(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return future, thread


@contextmanager
def blocked_append(monkeypatch, controller, *, boundary="fsync", kind="session.note",
                   payload=None):
    journal = controller.journal
    owner = threading.get_ident()
    gate = Gate()
    original = getattr(os, boundary)
    descriptor = journal._fd

    def held(fd, *args):
        if fd == descriptor and not gate.entered.is_set():
            assert threading.get_ident() != owner
            gate.pause()
        return original(fd, *args)

    with monkeypatch.context() as patch:
        patch.setattr(os, boundary, held)
        future, thread = background(lambda: journal.append(
            kind, payload or {"text": "committed after release"}, controller.loop.state,
        ))
        try:
            assert gate.entered.wait(5)
            yield gate
        finally:
            gate.release.set()
            thread.join(5)
            assert not thread.is_alive() and not gate.expired
            future.result(0)


def record_task(journal, context, name, *, terminal=True, native=False):
    state = RunState(context, task={"id": name, "status": "executing", "pending_calls": []})
    started = journal.append("task.started", {"text": name, "context": context.record()}, state)
    if native:
        journal.append("codex.turn.bound", {"native": {
            "thread_id": "thread", "turn_id": name, "item_id": "", "item_type": "",
        }}, state)
    if terminal:
        journal.append("model.completed", {"text": "answer " + name}, state)
        state.task["status"] = "completed"
        journal.append("task.completed", {}, state)
    return started, state


def history_item(journal, turn="old", item="evidence"):
    native = {"thread_id": "thread", "turn_id": turn,
              "item_id": item, "item_type": "webSearch"}
    origins = journal.native_origins(len(journal.records))
    return {"native": native, "origin": deepcopy(origins.origin(native)),
            "status": "recorded", "value": {}}
