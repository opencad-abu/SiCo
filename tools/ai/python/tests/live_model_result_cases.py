"""Live-model runner failures, publication and isolated result snapshots."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest
from live_model_fixture import make_session, passing_result, source_message, start_message, wait_for
from live_model_fixture import sessions as sessions

from cadai.live_model import MAX_EVENT_HISTORY, LiveModelSession


@pytest.mark.parametrize(
    "error, expected_code",
    [
        (TimeoutError("provider timed out"), "runner_timeout"),
        (RuntimeError("provider failed"), "runner_error"),
    ],
)
def test_runner_failures_are_typed_and_fail_closed(
    sessions: list[LiveModelSession], error: Exception, expected_code: str
) -> None:
    def runner(_config: Mapping[str, Any], _active: Mapping[str, Any]) -> Mapping[str, Any]:
        raise error

    session = make_session(sessions, runner)
    session.start(start_message())
    session.source_changed(source_message(1))
    wait_for(lambda: session.state == "ERROR")
    assert session.status()["result"]["code"] == expected_code


def test_typed_provider_unavailable_code_is_preserved(
    sessions: list[LiveModelSession],
) -> None:
    class ProviderUnavailable(RuntimeError):
        code = "MODEL_PROVIDER_UNAVAILABLE"

    def runner(_config: Mapping[str, Any], _active: Mapping[str, Any]) -> Mapping[str, Any]:
        raise ProviderUnavailable("no configured endpoint")

    session = make_session(sessions, runner)
    session.start(start_message())
    session.source_changed(source_message(1))
    wait_for(lambda: session.state == "ERROR")
    assert session.status()["result"]["code"] == "MODEL_PROVIDER_UNAVAILABLE"


def test_malformed_runner_result_is_rejected_without_retry(
    sessions: list[LiveModelSession],
) -> None:
    calls = 0

    def runner(_config: Mapping[str, Any], _active: Mapping[str, Any]) -> Any:
        nonlocal calls
        calls += 1
        return ["not", "an", "object"]

    session = make_session(sessions, runner)
    session.start(start_message())
    session.source_changed(source_message(1))
    wait_for(lambda: session.state == "ERROR")
    assert calls == 1
    assert session.status()["result"]["code"] == "invalid_result"


def test_event_sink_disconnect_does_not_kill_worker_or_retry(
    sessions: list[LiveModelSession],
) -> None:
    calls = 0

    def runner(_config: Mapping[str, Any], active: Mapping[str, Any]) -> Mapping[str, Any]:
        nonlocal calls
        calls += 1
        return passing_result(active)

    def disconnected(_event: Mapping[str, Any]) -> None:
        raise BrokenPipeError("Virtuoso disconnected")

    session = make_session(sessions, runner, event_sink=disconnected)
    session.start(start_message())
    session.source_changed(source_message(1))
    wait_for(lambda: session.state == "AWAITING_APPROVAL")
    assert calls == 1
    assert session.events(1)[0]["code"] == "candidate_ready"


def test_history_is_bounded_to_128_independent_snapshots(
    sessions: list[LiveModelSession],
) -> None:
    session = make_session(sessions, lambda _config, active: passing_result(active))
    session.start(start_message())
    for _index in range(MAX_EVENT_HISTORY + 25):
        session.handle({"event": "live_model.status"})

    events = session.events(MAX_EVENT_HISTORY)
    assert len(events) == MAX_EVENT_HISTORY
    assert events[0]["event_sequence"] > 1
    assert events[-1]["event_sequence"] - events[0]["event_sequence"] == 127

    events[0]["state"] = "MUTATED"
    assert session.events(MAX_EVENT_HISTORY)[0]["state"] != "MUTATED"


def test_runner_arguments_and_public_snapshots_are_deeply_isolated(
    sessions: list[LiveModelSession],
) -> None:
    sink_values: list[dict[str, Any]] = []

    def sink(value: Mapping[str, Any]) -> None:
        received = dict(value)
        sink_values.append(received)
        if isinstance(received.get("target"), dict):
            received["target"]["cell"] = "SINK_MUTATION"

    def runner(config: Mapping[str, Any], active: Mapping[str, Any]) -> Mapping[str, Any]:
        config["target"]["cell"] = "RUNNER_MUTATION"  # type: ignore[index]
        active["view_identity"]["view_name"] = "RUNNER_MUTATION"  # type: ignore[index]
        return passing_result(active)

    session = make_session(sessions, runner, event_sink=sink)
    session.start(start_message())
    session.source_changed(source_message(1))
    wait_for(lambda: session.state == "AWAITING_APPROVAL")

    status = session.status()
    assert status["target"]["cell"] == "LDO"
    assert status["view_identity"]["view_name"] == "schematic"
    status["target"]["cell"] = "CALLER_MUTATION"
    status["result"]["artifacts"][0] = "CALLER_MUTATION"
    assert session.status()["target"]["cell"] == "LDO"
    assert session.status()["result"]["artifacts"] == ["candidate/model.sv"]

    history = session.events(128)
    candidate = next(item for item in history if item["code"] == "candidate_ready")
    candidate["result"]["artifacts"][0] = "HISTORY_MUTATION"
    assert session.events(1)[0]["result"]["artifacts"] == ["candidate/model.sv"]
    assert sink_values
    assert session.status()["target"]["cell"] == "LDO"


def test_publication_is_refused_without_publisher_and_can_be_cancelled(
    sessions: list[LiveModelSession],
) -> None:
    session = make_session(sessions, lambda _config, active: passing_result(active))
    session.start(start_message())

    refused = session.handle(
        {"event": "live_model.approve_publish", "approval_id": "approval-1"}
    )
    assert refused["publication"] == "REFUSED_NO_PUBLISHER"
    assert refused["result"]["code"] == "publication_requires_separate_publisher"

    cancelled = session.handle({"event": "live_model.cancel_publish"})
    assert cancelled["publication"] == "CANCELLED"
    assert cancelled["result"]["code"] == "publication_cancelled"


def test_runner_secrets_are_redacted_from_all_observable_boundaries(
    sessions: list[LiveModelSession],
) -> None:
    leaked = "TOPSECRET0123456789"
    opaque = "sk-TOPSECRET0123456789"
    sink_values: list[dict[str, Any]] = []

    def runner(_config: Mapping[str, Any], active: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            "status": "ERROR api_key=%s" % leaked,
            "source_generation": active["source_generation"],
            "artifacts": ["Bearer %s" % leaked, opaque],
            "code": "token=%s" % leaked,
            "message": "authorization: Bearer %s; %s" % (leaked, opaque),
        }

    session = make_session(
        sessions,
        runner,
        event_sink=lambda value: sink_values.append(json.loads(json.dumps(value))),
    )
    session.start(start_message())
    session.source_changed(source_message(1))
    wait_for(lambda: session.state == "ERROR")

    observable = json.dumps(
        {
            "status": session.status(),
            "events": session.events(128),
            "sink": sink_values,
        },
        sort_keys=True,
    )
    assert leaked not in observable
    assert opaque not in observable
    assert "<redacted>" in observable


def test_runner_verdict_claim_is_a_protocol_error(
    sessions: list[LiveModelSession],
) -> None:
    session = make_session(
        sessions,
        lambda _config, _active: {"status": "PASS", "provider_verdict": "PASS"},
    )
    session.start(start_message())
    session.source_changed(source_message(1))
    wait_for(lambda: session.state == "ERROR")
    assert session.status()["result"]["code"] == "verdict_forbidden"
