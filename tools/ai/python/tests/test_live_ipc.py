from __future__ import annotations

import io
import json
import os
import threading
import time
from collections.abc import Callable, Mapping
from types import SimpleNamespace
from typing import Any

from cadai.controller import RequestDispatcher
from cadai.live_model import LiveModelSession
from cadai.mcp import McpServer
from cadai.terminal_control import TerminalControl, wait_for_terminal
from cadai.transport import VirtuosoTransport


TARGET = {"library": "live-lib", "cell": "LDO", "module": "LDO"}
VIEW = {
    "library": "live-lib",
    "cell": "LDO",
    "view_name": "schematic",
    "oa_view_type": "schematic",
    "oa_view_type_source": "platform_api",
    "kind": "schematic",
}


def start_message(*, recipe_id: str = "ldo-live") -> dict[str, Any]:
    return {
        "event": "live_model.start",
        "recipe_id": recipe_id,
        "through": "validate",
        "profile": "amsverify",
        "debounce_ms": 0,
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
        "snapshot_source_generation": "b" * 64,
        "validated_source_generation": "b" * 64,
        "artifacts": ["candidate/model.sv"],
    }


def wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition was not satisfied before timeout")


def _fake_terminal_process() -> SimpleNamespace:
    """Return a process handle that stays alive through one drain interval."""

    polls = 0

    def poll() -> int | None:
        nonlocal polls
        polls += 1
        return None if polls <= 2 else 0

    return SimpleNamespace(poll=poll)


def _json_line(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, separators=(",", ":")) + "\n").encode("utf-8")


def test_transport_routes_live_jsonl_and_returns_deep_copies() -> None:
    read_fd, write_fd = os.pipe()
    reader = os.fdopen(read_fd, "rb", buffering=0)
    transport = VirtuosoTransport(reader=reader, writer=io.BytesIO())
    try:
        transport.start()
        source = source_message(7)
        os.write(write_fd, b'{"event":"terminal.show"}\n')
        os.write(write_fd, _json_line(source))
        os.write(write_fd, _json_line(source))

        assert transport.next_event(timeout=1) == "terminal.show"
        first = transport.next_live_event(timeout=1)
        second = transport.next_live_event(timeout=1)
        assert first is not None and second is not None
        assert first["event"] == "live_model.source_changed"
        assert first["target"] == TARGET
        assert first is not second

        first["target"]["cell"] = "MUTATED"
        first["view_identity"]["view_name"] = "MUTATED"
        assert second["target"] == TARGET
        assert second["view_identity"] == VIEW
        assert transport.next_live_event(timeout=0) is None

        os.close(write_fd)
        write_fd = -1
        assert transport.wait_closed(timeout=1)
    finally:
        transport.close()
        if write_fd >= 0:
            os.close(write_fd)
        reader.close()


def test_terminal_wait_loop_consumes_live_events() -> None:
    read_fd, write_fd = os.pipe()
    reader = os.fdopen(read_fd, "rb", buffering=0)
    transport = VirtuosoTransport(reader=reader, writer=io.BytesIO())
    control = TerminalControl.create()
    session = LiveModelSession(
        runner=lambda _config, active: passing_result(active),
        session_id="terminal-live",
    )
    try:
        transport.start()
        os.write(write_fd, _json_line(start_message()))
        os.write(write_fd, _json_line(source_message(1)))
        control.close_reader()

        return_code, owner_shutdown = wait_for_terminal(
            _fake_terminal_process(),
            transport,
            control,
            threading.Event(),
            live_event_handler=session.handle,
        )

        assert return_code == 0
        assert owner_shutdown is False
        wait_for(lambda: session.state == "AWAITING_APPROVAL")
        status = session.status()
        assert status["session_id"] == "terminal-live"
        assert status["last_sequence"] == 1
        assert status["source_generation"] == "save-1"
    finally:
        session.close()
        control.close()
        transport.close()
        os.close(write_fd)
        reader.close()


def test_mcp_dispatch_reuses_one_controller_owned_live_session() -> None:
    session = LiveModelSession(
        runner=lambda _config, active: passing_result(active),
        session_id="mcp-live",
    )
    transport = VirtuosoTransport(reader=io.BytesIO(), writer=io.BytesIO())
    dispatcher = RequestDispatcher(
        transport,
        SimpleNamespace(socket=None, spool=None),
        workspace=None,
        live_session=session,
    )

    class ControllerClient:
        def call(self, method: str, arguments: dict[str, Any]):
            return True, dispatcher("mcp-live", method, arguments)

    server = McpServer(ControllerClient())  # type: ignore[arg-type]

    def tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": name,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        assert response is not None
        assert response["result"]["isError"] is False
        return json.loads(response["result"]["content"][0]["text"])

    try:
        started = tool("live_model_start", start_message())
        assert started["session_id"] == "mcp-live"
        assert started["recipe_id"] == "ldo-live"

        queued = tool("live_model_source_changed", source_message(3))
        assert queued["state"] == "DEBOUNCING"
        wait_for(lambda: session.state == "AWAITING_APPROVAL")

        status = tool("live_model_status", {})
        assert status["session_id"] == "mcp-live"
        assert status["state"] == "AWAITING_APPROVAL"
        assert status["source_generation"] == "save-3"
        assert dispatcher.live_session is session
    finally:
        session.close()
        transport.close()


def test_event_sink_disconnect_does_not_kill_worker_or_lifecycle_loop() -> None:
    calls: list[int] = []

    def runner(_config: Mapping[str, Any], active: Mapping[str, Any]):
        calls.append(active["sequence"])
        return passing_result(active)

    def disconnected_sink(_event: Mapping[str, Any]) -> None:
        raise BrokenPipeError("Virtuoso event sink disconnected")

    transport = VirtuosoTransport(reader=io.BytesIO(), writer=io.BytesIO())
    control = TerminalControl.create()
    session = LiveModelSession(
        runner=runner,
        event_sink=disconnected_sink,
        session_id="disconnect-live",
    )
    try:
        assert transport.route_event(start_message()) is True
        assert transport.route_event(source_message(1)) is True
        control.close_reader()

        return_code, owner_shutdown = wait_for_terminal(
            _fake_terminal_process(),
            transport,
            control,
            threading.Event(),
            live_event_handler=session.handle,
        )

        assert return_code == 0
        assert owner_shutdown is False
        wait_for(lambda: session.state == "AWAITING_APPROVAL")
        assert calls == [1]
        assert any(event["code"] == "candidate_ready" for event in session.events(128))
    finally:
        session.close()
        control.close()
        transport.close()


def test_stop_restart_discards_late_result_through_controller_dispatcher() -> None:
    entered = threading.Event()
    calls: list[tuple[str, int]] = []

    def runner(config: Mapping[str, Any], active: Mapping[str, Any]):
        recipe_id = config["recipe_id"]
        sequence = active["sequence"]
        calls.append((recipe_id, sequence))
        if recipe_id == "recipe-one":
            entered.set()
            assert session.cancel_event.wait(2.0)
        return passing_result(active)

    session = LiveModelSession(runner=runner, session_id="restart-live")
    transport = VirtuosoTransport(reader=io.BytesIO(), writer=io.BytesIO())
    dispatcher = RequestDispatcher(
        transport,
        SimpleNamespace(socket=None, spool=None),
        workspace=None,
        live_session=session,
    )
    try:
        assert dispatcher("live-1", "live_model_start", start_message(recipe_id="recipe-one"))[
            "recipe_id"
        ] == "recipe-one"
        assert dispatcher("live-2", "live_model_source_changed", source_message(1))[
            "state"
        ] == "DEBOUNCING"
        assert entered.wait(1.0)

        stopped = dispatcher("live-3", "live_model_stop", {})
        assert stopped["state"] == "STOPPED"
        assert "candidate_ready" not in [event["code"] for event in session.events(128)]

        restarted = dispatcher(
            "live-4", "live_model_start", start_message(recipe_id="recipe-two")
        )
        assert restarted["state"] == "IDLE"
        assert restarted["last_sequence"] == 0
        assert dispatcher("live-5", "live_model_source_changed", source_message(1, "restart-1"))[
            "state"
        ] == "DEBOUNCING"

        wait_for(lambda: session.state == "AWAITING_APPROVAL")
        assert calls == [("recipe-one", 1), ("recipe-two", 1)]
        assert [event["code"] for event in session.events(128)].count("candidate_ready") == 1
        assert session.status()["source_generation"] == "restart-1"
    finally:
        session.close()
        transport.close()
