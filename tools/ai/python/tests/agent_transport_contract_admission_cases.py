# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""agent transport contract admission contracts."""

from __future__ import annotations

import json
import threading
import time

import pytest

from sico.transport.broker import skill_call_context
from sico.transport.methods import QueryUnavailable
from sico.transport.router import InstanceRegistry, SkillInstanceRouter


def test_instance_router_reports_queue_and_unknown_without_waiting():
    router = SkillInstanceRouter("i", "g")
    entered = threading.Event()
    release = threading.Event()

    def operation(_request_id):
        entered.set()
        release.wait(2)
        return {"ok": True}

    first = threading.Thread(target=lambda: router.execute("get_context", operation))
    first.start()
    assert entered.wait(1)
    with pytest.raises(Exception) as queued:
        router.execute("get_context", operation)
    assert getattr(queued.value, "code", None) == "skill_queued"
    release.set()
    first.join(2)
    assert not first.is_alive()

    def timed_out(_request_id):
        raise RuntimeError("skill_timeout")

    with pytest.raises(RuntimeError):
        router.execute("get_context", timed_out)
    with pytest.raises(Exception) as blocked:
        router.execute("get_context", operation)
    assert getattr(blocked.value, "code", None) == "skill_blocked_unknown"
    unknown = router.snapshot()["unknown"]
    router.clear_unknown(unknown)
    assert router.snapshot()["unknown"] is None


def test_instance_registry_replaces_old_generation_and_exposes_snapshots():
    registry = InstanceRegistry()
    old = registry.register("instance", "generation-old")
    assert registry.register("instance", "generation-old") is old
    new = registry.register("instance", "generation-new")
    assert new is registry.get("instance", "generation-new")
    assert registry.get("instance", "generation-old") is None
    assert old.snapshot()["closed"] is True
    snapshots = registry.snapshot()
    assert len(snapshots) == 1
    assert snapshots[0]["router_id"] == new.router_id
    registry.close()
    assert new.snapshot()["closed"] is True


def test_session_request_gets_bounded_queue_admission():
    router = SkillInstanceRouter("i", "g")
    entered = threading.Event()
    release = threading.Event()
    queued = threading.Event()
    states = []

    def operation(_request_id):
        entered.set()
        release.wait(2)
        return {"ok": True}

    def progress(record):
        states.append(record)
        if record["state"] == "queued":
            queued.set()

    first = threading.Thread(target=lambda: router.execute("eval_skill", operation))
    first.start()
    assert entered.wait(1)
    with skill_call_context(session_id="session-b"):
        second = threading.Thread(target=lambda: router.execute(
            "eval_skill", operation, wait=True,
            progress=progress,
        ))
        second.start()
    assert queued.wait(1)
    assert second.is_alive()
    release.set()
    first.join(2)
    second.join(2)
    assert not second.is_alive()
    assert any(record["state"] == "completed" for record in states)


def test_router_status_records_only_json_safe_identity():
    records = []
    router = SkillInstanceRouter("i", "g")
    router.execute(
        "get_context", lambda _request_id: {"ok": True},
        session_id="session", progress=records.append,
    )
    assert records[0]["state"] == "accepted"
    assert records[-1]["state"] == "completed"
    assert all(callable(value) is False for value in records[-1].values())
    json.dumps(records[-1], allow_nan=False)
    assert records[-1]["router_id"] == router.router_id
    assert records[-1]["execution_mode"] == "foreground"


def test_router_reports_blocked_unknown_before_admission():
    records = []
    router = SkillInstanceRouter("i", "g")
    router.mark_unknown("request-unknown")
    with pytest.raises(QueryUnavailable) as raised:
        router.execute("get_context", lambda _request_id: {"ok": True}, progress=records.append)
    assert raised.value.code == "skill_blocked_unknown"
    assert records[-1]["state"] == "blocked_unknown"
    assert records[-1]["code"] == "skill_blocked_unknown"


def test_router_close_finishes_queued_request_without_dispatch():
    router = SkillInstanceRouter("i", "g")
    entered, release = threading.Event(), threading.Event()
    states = []

    def operation(_request_id):
        entered.set()
        release.wait(2)
        return {"ok": True}

    active_error = []

    def active():
        try:
            router.execute("read", operation)
        except QueryUnavailable as exc:
            active_error.append(exc.code)

    first = threading.Thread(target=active)
    first.start()
    assert entered.wait(1)
    error = {}

    def queued():
        try:
            router.execute("read", operation, wait=True, progress=states.append)
        except Exception as exc:
            error["value"] = exc

    second = threading.Thread(target=queued)
    second.start()
    time.sleep(0.05)
    router.close()
    second.join(2)
    release.set()
    first.join(2)
    assert not second.is_alive()
    assert active_error == ["router_unavailable"]
    assert getattr(error["value"], "code", None) == "router_unavailable"
    assert any(record["state"] == "router_unavailable" for record in states)
    assert router.snapshot()["closed"]


def test_router_queue_timeout_and_cancel_are_terminal_once():
    router = SkillInstanceRouter("i", "g")
    entered, release = threading.Event(), threading.Event()

    def operation(_request_id):
        entered.set()
        release.wait(2)
        return {"ok": True}

    first = threading.Thread(target=lambda: router.execute("read", operation))
    first.start()
    assert entered.wait(1)
    timeout_states, cancel_states = [], []
    errors = {}

    def queued_call(key, states, **kwargs):
        try:
            router.execute("read", operation, wait=True, progress=states.append, **kwargs)
        except Exception as exc:
            errors[key] = exc

    cancelled = threading.Event()
    timeout = threading.Thread(target=queued_call, args=("timeout", timeout_states),
                               kwargs={"queue_timeout": 0.05})
    cancel = threading.Thread(target=queued_call, args=("cancel", cancel_states),
                              kwargs={"cancelled": cancelled.is_set})
    timeout.start()
    timeout.join(1)
    cancelled.set()
    cancel.start()
    cancel.join(1)
    release.set()
    first.join(2)
    assert errors["timeout"].code == "skill_queue_timeout"
    assert errors["cancel"].code == "cancelled_before_start"
    assert [row["state"] for row in timeout_states].count("queue_timeout") == 1
    assert [row["state"] for row in cancel_states].count("cancelled_before_start") == 1
