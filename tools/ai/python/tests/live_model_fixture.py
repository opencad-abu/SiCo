"""Shared live-model message fixtures and session cleanup ownership."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

import pytest

from cadai.live_model import LiveModelProtocolError, LiveModelSession

TARGET = {"library": "lib", "cell": "LDO", "module": "LDO"}


VIEW = {
    "library": "lib",
    "cell": "LDO",
    "view_name": "schematic",
    "oa_view_type": "schematic",
    "oa_view_type_source": "platform_api",
    "kind": "schematic",
}


def start_message(*, debounce_ms: int = 0, recipe_id: str = "ldo-live") -> dict[str, Any]:
    return {
        "event": "live_model.start",
        "recipe_id": recipe_id,
        "through": "validate",
        "profile": "amsverify",
        "debounce_ms": debounce_ms,
        "target": dict(TARGET),
        "view_identity": dict(VIEW),
    }


def source_message(sequence: int, generation: str | None = None) -> dict[str, Any]:
    return {
        "event": "live_model.source_changed",
        "sequence": sequence,
        "source_generation": generation or "save-%d" % sequence,
        "target": dict(TARGET),
        "view_identity": dict(VIEW),
        "changed_kind": "schematic",
    }


def passing_result(active: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": "PASS",
        "run_id": "run-%s" % active["sequence"],
        "requested_source_generation": active["source_generation"],
        "snapshot_source_generation": "a" * 64,
        "validated_source_generation": "a" * 64,
        "artifacts": ["candidate/model.sv"],
    }


def wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition was not satisfied before timeout")


@pytest.fixture
def sessions() -> list[LiveModelSession]:
    active: list[LiveModelSession] = []
    yield active
    for session in active:
        session.close()


def make_session(
    sessions: list[LiveModelSession],
    runner: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]],
    **kwargs: Any,
) -> LiveModelSession:
    session = LiveModelSession(runner=runner, session_id="live-test", **kwargs)
    sessions.append(session)
    return session


def assert_protocol_code(code: str, callback: Callable[[], Any]) -> None:
    with pytest.raises(LiveModelProtocolError) as captured:
        callback()
    assert captured.value.code == code
