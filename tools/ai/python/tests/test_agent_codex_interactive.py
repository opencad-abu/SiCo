"""Reuse native Virtuoso interaction contracts with real Codex and a local model."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager

import pytest
import test_agent_ade_desktop_ic231 as ade
import test_agent_quick_ic231 as quick
import test_agent_rmb_ic231 as rmb
from agent_gui_support import isolated_display
from test_agent_codex_native import answer, gateway, native_codex, sse  # noqa: F401

pytestmark = pytest.mark.skipif(
    os.environ.get("SICO_IC_TESTS") != "1", reason="Opt-in IC231 Codex desktop acceptance"
)


@pytest.mark.parametrize(
    "scenario,tool_format",
    [
        ("routing", "flat"),
        ("schematic", "flat"),
        ("layout", "native"),
        ("results", "flat"),
        ("history", "native"),
    ],
)
def test_codex_native_interaction_contract(
    tmp_path,
    native_codex,  # noqa: F811
    gateway,  # noqa: F811
    monkeypatch,
    scenario,
    tool_format,
):
    if scenario in {"results", "history"}:
        for index, name in enumerate(("read_ade_setup", "read_ade_history")):
            identity = (
                {"name": "mcp__sico_ic__" + name}
                if tool_format == "flat"
                else {"namespace": "mcp__sico_ic", "name": name}
            )
            gateway.responses.put(
                sse(
                    {
                        "type": "function_call",
                        "id": f"fc-{index}",
                        "call_id": f"call-{index}",
                        "arguments": "{}",
                        **identity,
                    }
                )
            )
            gateway.responses.put(answer("ADE 数据已读取，详见工具数据。"))
    else:
        # Direct answers use the deterministic source refresh on each task.
        for _ in range(16):
            gateway.responses.put(answer("Codex Copilot: captured source received."))

    @contextmanager
    def configured_display(directory):
        with isolated_display(directory) as env:
            env.update(
                SICO_BACKEND="codex",
                SICO_PROVIDER="responses",
                SICO_API_URL=f"http://127.0.0.1:{gateway.server_port}/v1",
                SICO_MODEL="cad-copilot-test",
                SICO_CODEX_CLI=native_codex,
                SICO_TOOL_FORMAT=tool_format,
                SICO_API_KEY="copilot-test-secret",
                NO_PROXY="127.0.0.1,localhost",
                no_proxy="127.0.0.1,localhost",
            )
            yield env

    for module in (quick, rmb, ade):
        monkeypatch.setattr(module, "isolated_display", configured_display)
    if scenario == "routing":
        quick.test_window_session_binding_and_explicit_new_session(tmp_path)
    elif scenario in {"schematic", "layout"}:
        rmb.test_native_rmb_preserves_site_menu_and_bound_target(tmp_path, scenario, False)
    else:
        ade.test_ade_desktop_task_and_hidden_session_restore(tmp_path, scenario)
    assert gateway.requests
    for request in gateway.requests:
        assert any(t["type"] == "namespace" for t in request["tools"]) is (tool_format == "native")
    for path in (tmp_path / ".cad/ai/agent/sessions").glob("*/events.jsonl"):
        events = [json.loads(line) for line in path.read_text().splitlines()]
        assert sum(e["kind"] == "codex.thread" for e in events) == 1
        assert all(not e["state"]["messages"] for e in events)
        assert "copilot-test-secret" not in path.read_text()
