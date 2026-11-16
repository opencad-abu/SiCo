"""Live-model scheduling, generations and stop/restart lifecycle."""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from typing import Any

from live_model_fixture import make_session, passing_result, source_message, start_message, wait_for
from live_model_fixture import sessions as sessions

from cadai.live_model import LiveModelSession


def test_configured_identity_mismatch_and_duplicate_sequence_are_rejected(
    sessions: list[LiveModelSession],
) -> None:
    calls: list[int] = []
    session = make_session(
        sessions,
        lambda _config, active: calls.append(active["sequence"]) or passing_result(active),
    )
    session.start(start_message(debounce_ms=50))

    wrong_target = source_message(1)
    wrong_target["target"]["module"] = "OTHER"
    status = session.source_changed(wrong_target)
    assert status["last_sequence"] == 0
    assert session.events(1)[0]["code"] == "target_mismatch"

    wrong_view = source_message(1)
    wrong_view["view_identity"]["oa_view_type"] = "schematicXL"
    status = session.source_changed(wrong_view)
    assert status["last_sequence"] == 0
    assert session.events(1)[0]["code"] == "view_mismatch"

    session.source_changed(source_message(1))
    duplicate = session.source_changed(source_message(1))
    assert duplicate["last_sequence"] == 1
    assert session.events(1)[0]["code"] == "duplicate_sequence"
    wait_for(lambda: session.state == "AWAITING_APPROVAL")
    assert calls == [1]


def test_debounce_coalesces_to_the_latest_saved_source(
    sessions: list[LiveModelSession],
) -> None:
    calls: list[tuple[int, str]] = []

    def runner(_config: Mapping[str, Any], active: Mapping[str, Any]) -> Mapping[str, Any]:
        calls.append((active["sequence"], active["source_generation"]))
        return passing_result(active)

    session = make_session(sessions, runner)
    session.start(start_message(debounce_ms=80))
    session.source_changed(source_message(1))
    time.sleep(0.015)
    session.source_changed(source_message(2))

    wait_for(lambda: session.state == "AWAITING_APPROVAL")
    assert calls == [(2, "save-2")]
    status = session.status()
    assert status["last_sequence"] == 2
    assert status["source_generation"] == "save-2"


def test_newer_save_marks_late_result_stale_and_runs_latest_source(
    sessions: list[LiveModelSession],
) -> None:
    first_entered = threading.Event()
    release_first = threading.Event()
    calls: list[int] = []

    def runner(_config: Mapping[str, Any], active: Mapping[str, Any]) -> Mapping[str, Any]:
        sequence = active["sequence"]
        calls.append(sequence)
        if sequence == 1:
            first_entered.set()
            assert release_first.wait(2.0)
        return passing_result(active)

    session = make_session(sessions, runner)
    session.start(start_message())
    session.source_changed(source_message(1))
    assert first_entered.wait(1.0)
    session.source_changed(source_message(2))
    release_first.set()

    wait_for(lambda: session.state == "AWAITING_APPROVAL")
    assert calls == [1, 2]
    assert any(event["code"] == "stale_source" for event in session.events(128))
    assert session.status()["source_generation"] == "save-2"


def test_active_session_cannot_be_reconfigured_without_stop(
    sessions: list[LiveModelSession],
) -> None:
    session = make_session(sessions, lambda _config, active: passing_result(active))
    first = session.start(start_message(recipe_id="recipe-one"))
    second = session.start(start_message(recipe_id="recipe-two"))

    assert first["recipe_id"] == "recipe-one"
    assert second["recipe_id"] == "recipe-one"
    assert second["result"]["code"] == "session_already_running"
    assert session.events(1)[0]["code"] == "session_already_running"


def test_stop_then_restart_resets_sequence_and_configuration(
    sessions: list[LiveModelSession],
) -> None:
    calls: list[tuple[str, int]] = []

    def runner(config: Mapping[str, Any], active: Mapping[str, Any]) -> Mapping[str, Any]:
        calls.append((config["recipe_id"], active["sequence"]))
        return passing_result(active)

    session = make_session(sessions, runner)
    session.start(start_message(recipe_id="recipe-one"))
    session.source_changed(source_message(1))
    wait_for(lambda: session.state == "AWAITING_APPROVAL")
    assert session.stop()["state"] == "STOPPED"

    restarted = session.start(start_message(recipe_id="recipe-two"))
    assert restarted["state"] == "IDLE"
    assert restarted["last_sequence"] == 0
    session.source_changed(source_message(1, "restart-1"))
    wait_for(lambda: session.state == "AWAITING_APPROVAL")
    assert calls == [("recipe-one", 1), ("recipe-two", 1)]


def test_stop_discards_late_runner_result_and_emits_no_candidate(
    sessions: list[LiveModelSession],
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def runner(_config: Mapping[str, Any], active: Mapping[str, Any]) -> Mapping[str, Any]:
        entered.set()
        assert release.wait(2.0)
        return passing_result(active)

    session = make_session(sessions, runner)
    session.start(start_message())
    session.source_changed(source_message(1))
    assert entered.wait(1.0)

    stopped: list[dict[str, Any]] = []
    stopper = threading.Thread(target=lambda: stopped.append(session.stop()))
    stopper.start()
    wait_for(lambda: session.cancel_event.is_set())
    release.set()
    stopper.join(timeout=2.0)
    assert not stopper.is_alive()
    assert stopped[0]["state"] == "STOPPED"
    codes = [event["code"] for event in session.events(128)]
    assert codes[-1] == "session_stopped"
    assert "candidate_ready" not in codes
