"""Recovered work is released only by an explicit per-input decision."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sico.core.contracts import BoundContext
from sico.service.recovered_session import RecoveredSession


CURRENT = BoundContext("instance", "generation", "target", {})
OTHER = BoundContext("instance-two", "generation", "target", {})


def fact(input_id, status="queued"):
    return {
        "input_id": input_id,
        "status": status,
        "address": "address-" + input_id,
    }


def record(input_id, context=CURRENT):
    return {
        "id": input_id,
        "message": {"context": context.record()},
    }


def facts(*rows):
    return SimpleNamespace(inputs=rows)


class Inbox:
    def __init__(self, directory):
        self.directory = directory
        self.pending = []
        self.recovered = []
        self.accept = True

    def recover(self, input_id):
        if not self.accept:
            return False
        row = record(input_id)
        self.recovered.append(input_id)
        self.pending.append(row)
        return True


class Events:
    def __init__(self):
        self.refreshes = 0
        self.state = {"invalidated": {}}

    def refresh(self):
        self.refreshes += 1


class Controller:
    def __init__(self, tmp_path):
        self.paused = False
        self.busy = False
        self.closing = False
        self.session_id = "session"
        self.runtime_id = "runtime"
        self.current = CURRENT
        self._task = {"status": "completed"}
        self.inbox = Inbox(tmp_path / "inbox")
        self.bindings = SimpleNamespace(events=Events(), sync_error="")
        self.history = SimpleNamespace(records=[], record=self.history_record)
        self.journal = SimpleNamespace(events=[], append_session=self.append_session)
        self.changes = 0
        self.resumes = 0

    def _changed(self):
        self.changes += 1

    def resume(self):
        self.resumes += 1

    def history_record(self, row):
        self.history.records.append(row)

    def append_session(self, event, payload, context):
        self.journal.events.append((event, payload, context))


@pytest.fixture
def controller(tmp_path):
    return Controller(tmp_path)


def install_facts(monkeypatch, *rows, records=None):
    monkeypatch.setattr(
        "sico.service.recovered_session.read_facts",
        lambda _project, _session_id: facts(*rows),
    )
    records = records or [record(row["input_id"]) for row in rows]
    by_id = {row["id"]: row for row in records}
    monkeypatch.setattr(
        "sico.service.recovered_session.read_input",
        lambda _directory, input_id, confirm=False: by_id.get(input_id),
    )


def test_construction_pauses_controller_and_starts_blocked(controller):
    recovery = RecoveredSession(controller, object())
    assert recovery.blocked is True
    assert controller.paused is True


def test_stage_requires_a_queued_input_with_the_same_captured_target(
    controller, monkeypatch
):
    recovery = RecoveredSession(controller, object())
    install_facts(monkeypatch, fact("one"))
    assert recovery.stage("one") is True
    assert controller.inbox.recovered == ["one"]
    assert controller.history.records == [record("one")]
    assert controller.changes == 1

    controller.busy = True
    with pytest.raises(ValueError, match="恢复阶段已结束"):
        recovery.stage("one")


@pytest.mark.parametrize(
    ("rows", "records", "message"),
    [
        ([], [], "该输入可能已开始执行"),
        ([fact("one", "executing")], [record("one")], "该输入可能已开始执行"),
        ([fact("one")], [record("one", context=OTHER)], "输入捕获目标不同"),
    ],
)
def test_stage_rejects_missing_started_or_different_capture(
    controller, monkeypatch, rows, records, message
):
    recovery = RecoveredSession(controller, object())
    install_facts(monkeypatch, *rows, records=records)
    with pytest.raises(ValueError, match=message):
        recovery.stage("one")


def test_stage_does_not_acknowledge_when_inbox_recovery_declines(
    controller, monkeypatch
):
    recovery = RecoveredSession(controller, object())
    install_facts(monkeypatch, fact("one"))
    controller.inbox.accept = False
    assert recovery.stage("one") is True
    assert controller.history.records == []
    assert controller.changes == 0


def test_continue_abandons_unselected_inputs_then_releases_and_resumes(
    controller, monkeypatch
):
    recovery = RecoveredSession(controller, object())
    selected = record("keep")
    controller.inbox.pending = [selected]
    install_facts(
        monkeypatch,
        fact("keep"),
        fact("old"),
        fact("keep"),
    )
    recovery.continue_chat()

    assert recovery.blocked is False
    assert controller.resumes == 1
    assert controller.inbox.pending == [selected]
    assert [row[0] for row in controller.journal.events] == [
        "session.input_abandoned",
        "session.recovery_released",
    ]
    assert controller.bindings.events.refreshes == 1


def test_continue_opens_the_reconcile_card_without_releasing(controller, monkeypatch):
    recovery = RecoveredSession(controller, object())
    controller._task = {"status": "needs_reconcile"}
    install_facts(monkeypatch, fact("queued"))
    recovery.continue_chat()
    assert recovery.blocked is True
    assert controller.resumes == 0
    assert controller.journal.events == []


def test_continue_is_idempotent_after_release(controller, monkeypatch):
    recovery = RecoveredSession(controller, object())
    recovery.blocked = False
    install_facts(monkeypatch)
    recovery.continue_chat()
    assert controller.resumes == 0


def test_abandon_is_explicit_and_absent_input_is_not_an_error(controller, monkeypatch):
    recovery = RecoveredSession(controller, object())
    install_facts(monkeypatch, fact("queued"))
    assert recovery.abandon("missing") is False
    assert recovery.abandon("queued") is True
    assert controller.inbox.pending == []
    assert [row[0] for row in controller.journal.events] == ["session.input_abandoned"]


def test_abandon_refuses_to_clear_an_active_task(controller, monkeypatch):
    recovery = RecoveredSession(controller, object())
    controller._task = {"status": "executing"}
    install_facts(monkeypatch, fact("queued"))
    with pytest.raises(ValueError, match="请先核对并确认原中断任务"):
        recovery.abandon("queued")
    assert controller.journal.events == []


@pytest.mark.parametrize(
    ("task", "pending", "rows", "message"),
    [
        ({"status": "executing"}, [], [fact("queued")], "请先逐项恢复"),
        ({"status": "completed"}, [], [fact("queued")], "请先逐项恢复"),
        ({"status": "completed"}, [record("queued")],
         [fact("queued", "executing")], "请先逐项恢复"),
    ],
)
def test_release_requires_every_durable_input_to_be_selected_and_queued(
    controller, monkeypatch, task, pending, rows, message
):
    recovery = RecoveredSession(controller, object())
    controller._task = task
    controller.inbox.pending = pending
    install_facts(monkeypatch, *rows)
    with pytest.raises(ValueError, match=message):
        recovery.release()
    assert recovery.blocked is True


def test_release_checks_capture_health_before_unblocking(controller, monkeypatch):
    recovery = RecoveredSession(controller, object())
    selected = record("queued")
    controller.inbox.pending = [selected]
    install_facts(monkeypatch, fact("queued"), records=[selected])
    controller.bindings.sync_error = "lost"
    with pytest.raises(ValueError, match="捕获来源不可用"):
        recovery.release()
    assert recovery.blocked is True

    controller.bindings.sync_error = ""
    controller.bindings.events.state = {"invalidated": {"target": "reason"}}
    with pytest.raises(ValueError, match="捕获来源不可用"):
        recovery.release()
    assert recovery.blocked is True


def test_release_unblocks_only_after_writing_the_release_fact(controller, monkeypatch):
    recovery = RecoveredSession(controller, object())
    selected = record("queued")
    controller.inbox.pending = [selected]
    install_facts(monkeypatch, fact("queued"), records=[selected])
    recovery.release()
    assert recovery.blocked is False
    assert controller.journal.events == [
        (
            "session.recovery_released",
            {"runtime_id": "runtime"},
            CURRENT,
        )
    ]
