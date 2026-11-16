"""Native failed turns preserve useful classifications without reflecting bodies."""
# ruff: noqa: F401, F811 -- pytest fixture registration and injection

import json
from types import SimpleNamespace

import pytest

from test_agent_codex_native import CONTEXT, answer, native_codex, settings, sse
from test_agent_codex_protocol import gateway
from sico.codex.backend import CodexBackend
from sico.codex.turn_error import failure_message
from sico.core.tools import ToolRegistry
from sico.storage.journal import SessionJournal


@pytest.mark.parametrize("info, expected", [
    ({"httpConnectionFailed": {"httpStatusCode": 401}}, "HTTP 401"),
    ({"responseStreamDisconnected": {"httpStatusCode": 502}}, "HTTP 502"),
    ("badRequest", "badRequest"),
    ("contextWindowExceeded", "上下文"),
    ("usageLimitExceeded", "配额"),
    ("unauthorized", "凭据"),
])
def test_classification_never_reflects_upstream_text(info, expected):
    message = failure_message({
        "codexErrorInfo": info, "message": "Bearer secret-token",
        "additionalDetails": "private prompt and endpoint",
    }, tool_format="flat")
    assert expected in message and "工具格式：flat" in message
    assert "secret-token" not in message and "private prompt" not in message


@pytest.mark.parametrize("error", [
    None, "secret-token", {"codexErrorInfo": "secret-token"},
    {"codexErrorInfo": {"secret-token": {"httpStatusCode": 401}}},
    {"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": "secret-token"}}},
    {"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": True}}},
    {"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 999}}},
])
def test_unknown_error_data_is_not_displayed(error):
    message = failure_message(error)
    assert "secret-token" not in message and "（HTTP " not in message
    assert "工具格式：native" in message


@pytest.mark.parametrize("tool_format", ["native", "switch"])
@pytest.mark.parametrize("code", [400, 401, 429, 503])
def test_real_codex_http_failure_reaches_task_without_replay_or_secrets(
        tmp_path, gateway, native_codex, tool_format, code):
    gateway.reply = (code, "application/json", json.dumps({"error": {
        "message": "copilot-test-secret private prompt",
        "code": "invalid_request_error",
    }}).encode())
    provider = settings(gateway, native_codex, tool_format)
    with SessionJournal(tmp_path, "failed-turn") as journal:
        backend = CodexBackend(provider, ToolRegistry(), journal, CONTEXT)
        try:
            events = list(backend.run("Synthetic failure probe", input_id="fail"))
            assert events[-1]["kind"] == "task.failed", events[-1]
            message = events[-1]["payload"]["message"]
            # 0.162.1 preserves native 401/503 HTTP statuses; this synthetic
            # native 400 body still arrives as `other` without a status.
            if tool_format == "switch" or code != 400:
                assert f"HTTP {code}" in message, message
            else:
                assert "[other]" in message, message
            assert ("工具格式：flat" if tool_format == "switch" else "工具格式：native") in message
            if tool_format == "switch":
                assert "兼容层：Model gateway returned HTTP" in message
            assert len(gateway.requests) == 1
            recorded = json.dumps(journal.events())
            assert "copilot-test-secret" not in recorded and "private prompt" not in recorded
        finally:
            backend.close()


def test_error_events_are_scoped_and_retry_notifications_do_not_end_turn(tmp_path, monkeypatch):
    from codex_thread_fixture import bind_thread

    with SessionJournal(tmp_path, "error-events") as journal:
        backend = CodexBackend(None, ToolRegistry(), journal, CONTEXT)
        bind_thread(backend)
        backend._begin("检查", CONTEXT, "input", "chat")
        loop = backend._turn_loop()
        thread = backend.thread_id
        error = {"message": "private prompt", "codexErrorInfo": "badRequest"}
        pending = iter([
            {"method": "error", "params": {
                "threadId": "other", "turnId": "turn", "error": error, "willRetry": False}},
            {"method": "error", "params": {
                "threadId": thread, "turnId": "old", "error": error, "willRetry": False}},
            {"method": "error", "params": {
                "threadId": thread, "turnId": "turn", "error": error, "willRetry": True}},
            {"method": "turn/completed", "params": {
                "threadId": thread, "turn": {"id": "turn", "status": "failed", "error": None}}},
        ])
        loop.rpc = SimpleNamespace(next=lambda: next(pending))
        def tick(*args):
            yield from ()
            return False, False, None
        monkeypatch.setattr(loop, "tick", tick)
        events = list(loop.consume("turn", lambda: False, idle_timeout=10, tool_timeout=10,
                                  max_output_chars=1000,
                                  known_notifications={"error", "turn/completed"}))
        assert "badRequest" not in events[-1]["payload"]["message"]


def test_flat_unknown_tool_name_is_answered_and_the_turn_continues(
        tmp_path, gateway, native_codex):
    # A help-listed but unadvertised name must not abort the stream: Codex
    # answers the call with its own "unsupported call" tool error and the
    # model continues, exactly like the native (non-flat) path.
    gateway.replies = [
        (200, "text/event-stream", sse({
            "type": "function_call", "id": "bad-call", "call_id": "bad-call",
            "name": "unknown-secret-tool", "arguments": "{}",
        })),
        (200, "text/event-stream", answer("RECOVERED")),
    ]
    with SessionJournal(tmp_path, "flat-stream") as journal:
        backend = CodexBackend(settings(gateway, native_codex, "switch"),
                               ToolRegistry(), journal, CONTEXT)
        try:
            events = list(backend.run("Synthetic stream probe", input_id="stream"))
            assert events[-1]["kind"] == "task.completed", events[-1]
            assert len(gateway.requests) == 2
            followup = json.dumps(gateway.requests[1])
            assert "unsupported call: unknown-secret-tool" in followup
            assert "兼容层" not in events[-1]["payload"].get("message", "")
        finally:
            backend.close()


def test_error_notification_is_retained_when_completion_has_no_details(tmp_path):
    from codex_thread_fixture import bind_thread

    with SessionJournal(tmp_path, "error-only") as journal:
        backend = CodexBackend(None, ToolRegistry(), journal, CONTEXT)
        bind_thread(backend)
        backend._begin("检查", CONTEXT, "input", "chat")
        loop = backend._turn_loop()
        loop.failure = failure_message({"codexErrorInfo": "unauthorized"})
        loop.native_turn_ended("turn", {"turn": {"status": "failed", "error": None}}, lambda: False)
        events = list(loop.settle("turn", "failed", lambda: False))
        assert "unauthorized" in events[-1]["payload"]["message"]


def test_lsf_diagnostic_is_available_in_both_tool_formats():
    for mode in ("flat", "native"):
        gateway = SimpleNamespace(diagnostic="LSF AI job cleanup unconfirmed")
        message = failure_message(gateway=gateway, tool_format=mode)
        assert "LSF 网关：LSF AI job cleanup unconfirmed" in message


def test_other_uses_locally_observed_http_status_for_guidance():
    wrapper = SimpleNamespace(http_status=400, diagnostic='Model gateway returned HTTP 400')
    message = failure_message({'codexErrorInfo': 'other'}, wrapper=wrapper, tool_format='flat')
    assert '（HTTP 400）' in message and '请求字段/工具类型' in message
