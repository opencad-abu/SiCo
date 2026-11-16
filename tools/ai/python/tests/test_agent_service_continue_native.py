"""An explicitly continued archive retains its original native Codex thread."""

import json
from contextlib import ExitStack

import pytest
from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service
from agent_service_recovery_fixtures import client, seed
from test_agent_codex_native import answer, gateway, native_codex, settings, sse  # noqa: F401

from sico.codex.backend import CodexBackend


@pytest.mark.parametrize("tool_format", ["native", "flat"])
@pytest.mark.parametrize("offline", [False, True, "new_host"])
def test_continuation_resumes_the_same_thread_without_replaying_old_input(
        tmp_path, monkeypatch, gateway, native_codex, tool_format, offline):
    provider = settings(gateway, native_codex, tool_format)
    provider.release = None
    seed(tmp_path, monkeypatch, config=provider.options, environment=provider.environment)
    if offline:
        from sico.service.session_recovery import SessionRecovery

        def unavailable(*args):
            raise ConnectionRefusedError("old host has gone")
        monkeypatch.setattr(SessionRecovery, "_bridge", unavailable)
    monkeypatch.setattr("sico.service.project_sessions.create_backend",
                        lambda *args: CodexBackend(*args))
    gateway.responses.put(answer("Remembered original constraint 17."))
    if offline == "new_host":
        name = ({"namespace": "mcp__sico_ic", "name": "get_context"}
                if tool_format == "native" else {"name": "mcp__sico_ic__get_context"})
        gateway.responses.put(sse(dict(type="function_call", id="fc-current",
            call_id="call-current", arguments="{}", **name)))
    gateway.responses.put(answer("Continued with original constraint 17."))
    with business_service(tmp_path, monkeypatch, provider=provider) as service, ExitStack() as stack:
        service.owner._default_config = provider.options
        service.owner._default_environment = provider.environment
        api = client(stack, service, tmp_path)
        original_thread = None
        original_runtime = None
        for index, text in enumerate(("Remember original constraint 17.", "Continue the analysis.")):
            if index and offline == "new_host":
                from test_agent_service_continue_host import live_host

                _, current = live_host(stack, tmp_path)
            token = api.recovery.continue_session("old").result(5)
            controller = service.owner.controllers["old"]
            assert not controller.paused and not controller.inbox.pending
            assert len(gateway.requests) == index
            if index:
                assert controller.loop.thread_id == original_thread
                assert controller.runtime_id != original_runtime
                if offline == "new_host":
                    from test_agent_service_continue_host import read_tool

                    assert controller.current == current
                    read_tool(controller)
            assert api.can_control(token)
            api.command(token, "submit", text).result(5)
            wait_until(lambda: not controller.busy, timeout=15)
            assert controller._task["status"] == "completed"
            assert len(gateway.requests) == index + 1 + int(index and offline == "new_host")
            original_thread = controller.loop.thread_id
            original_runtime = controller.runtime_id
            api.end_session(token, "end-" + str(index)).result(5)
            wait_until(lambda: "old" not in service.owner.controllers)
        assert "Remember original constraint 17." in json.dumps(gateway.requests[1])
        assert "Continue the analysis." in json.dumps(gateway.requests[1])
        if offline == "new_host":
            outputs = [row for row in gateway.requests[2]["input"]
                       if row.get("type") == "function_call_output"
                       and row.get("call_id") == "call-current"]
            assert len(outputs) == 1 and 'ciw' in json.dumps(outputs[0])
