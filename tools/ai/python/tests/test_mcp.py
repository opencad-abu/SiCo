from __future__ import annotations

import io
import json
from types import SimpleNamespace
from typing import Any

import cadai.mcp as mcp_module
from cadai.workflows.verification import mcp_aivw_handler
from cadai.mcp import mcp_transport
from cadai.mcp import TOOLS, McpServer, SocketClient
from cadai.platform.io.protocol import decode_line, encode_line
from cadai.platform.io.runtime import MAX_EVAL_BYTES, RuntimePaths


class FakeClient:
    def __init__(self, ok: bool = True):
        self.ok = ok
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, method: str, arguments: dict[str, Any]):
        self.calls.append((method, arguments))
        if self.ok:
            return True, {"ok": True, "value": "3"}
        return False, {"code": "skill_error", "message": "rejected"}

    def close(self) -> None:
        pass


def test_initialize_exposes_instructions_and_tools_capability() -> None:
    server = McpServer(FakeClient())
    response = server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "x"}}
    )
    assert response["result"]["protocolVersion"] == "x"
    assert "当前 Virtuoso 进程" in response["result"]["instructions"]
    assert response["result"]["capabilities"] == {"tools": {"listChanged": False}}


def test_verification_profile_filters_tools_and_rejects_hidden_calls() -> None:
    server = McpServer(FakeClient(), profile="verification")
    listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {tool["name"] for tool in listed["result"]["tools"]}
    assert "get_context" in names
    assert "run_aivw_recipe" in names
    assert "eval_skill" not in names
    assert "inspect_layout" not in names
    assert "inspect_config_binding" in names
    rejected = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "eval_skill", "arguments": {"code": "nil"}},
        }
    )
    assert rejected["error"]["code"] == -32602
    initialized = server.handle(
        {"jsonrpc": "2.0", "id": 3, "method": "initialize", "params": {}}
    )
    assert "eval_skill" not in initialized["result"]["instructions"]
    assert "这是 AIVW 校验配置" in initialized["result"]["instructions"]


def test_verification_candidate_ledger_tracks_revision_and_restores(tmp_path) -> None:
    root = tmp_path / "runtime"
    spool = root / "spool"
    spool.mkdir(parents=True)
    runtime = RuntimePaths(root, root / "bridge.sock", spool)

    class CandidateClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.runtime = runtime

    generation = "a" * 64
    server = McpServer(CandidateClient(), workspace=tmp_path, profile="verification")
    rejected = server.handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "submit_candidate", "arguments": {
            "source": "module top; endmodule", "module": "top",
            "language": "systemverilog", "source_generation": generation,
            "verdict": "PASS",
        }},
    })
    assert rejected["error"]["code"] == -32602
    submitted = server.handle({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "submit_candidate", "arguments": {
            "source": "module top; endmodule", "module": "top",
            "language": "systemverilog", "source_generation": generation,
        }},
    })
    metadata = json.loads(submitted["result"]["content"][0]["text"])
    candidate = metadata["candidate_sha256"]
    assert metadata["revision"] == 0 and "source" not in metadata
    revised = server.handle({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "submit_candidate", "arguments": {
            "source": "module top; assign out = 1'b0; endmodule", "module": "top",
            "language": "systemverilog", "source_generation": generation,
            "parent_sha256": candidate,
        }},
    })
    revised_metadata = json.loads(revised["result"]["content"][0]["text"])
    assert revised_metadata["revision"] == 1
    restored = McpServer(CandidateClient(), workspace=tmp_path, profile="verification")
    fetched = restored.handle({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "get_candidate", "arguments": {"candidate_sha256": candidate}},
    })
    assert json.loads(fetched["result"]["content"][0]["text"])["candidate_sha256"] == candidate


def test_tool_annotations_drive_codex_approval_policy() -> None:
    tools = {tool["name"]: tool for tool in TOOLS}
    assert tools["get_context"]["annotations"]["readOnlyHint"] is True
    assert tools["get_context"]["annotations"]["destructiveHint"] is False
    for name in ("search_skill_api", "get_skill_api"):
        assert tools[name]["annotations"]["readOnlyHint"] is True
        assert tools[name]["annotations"]["destructiveHint"] is False
    assert tools["eval_skill"]["annotations"]["destructiveHint"] is True
    assert tools["load_skill_file"]["annotations"]["destructiveHint"] is True
    assert tools["run_aivw_recipe"]["annotations"] == {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }
    assert tools["eval_skill"]["inputSchema"]["properties"]["code"]["maxLength"] == 65_536


def test_aivw_recipe_tool_runs_locally_without_forwarding_token_to_tool_result(
    tmp_path, monkeypatch
) -> None:
    client = FakeClient()

    def fake_runner(arguments, *, client, workspace):
        assert arguments == {"reference": "saradc.comparator_new", "through": "snapshot"}
        assert workspace == tmp_path
        return {
            "ok": True,
            "aivw": {"status": "PASS"},
            "delegation": {"credential_exposed_to_agent": False},
        }

    monkeypatch.setattr(mcp_aivw_handler, "run_aivw_recipe", fake_runner)
    response = McpServer(client, workspace=tmp_path).handle(
        {
            "jsonrpc": "2.0",
            "id": "aivw-1",
            "method": "tools/call",
            "params": {
                "name": "run_aivw_recipe",
                "arguments": {
                    "reference": "saradc.comparator_new",
                    "through": "snapshot",
                },
            },
        }
    )

    assert response["result"]["isError"] is False
    assert "credential_exposed_to_agent" in response["result"]["content"][0]["text"]
    assert client.calls == []


def test_aivw_recipe_binds_feedback_to_candidate_and_rejects_stale_generation(
    tmp_path, monkeypatch
):
    runtime = RuntimePaths(
        tmp_path / "runtime",
        tmp_path / "runtime" / "bridge.sock",
        tmp_path / "runtime" / "spool",
    )
    runtime.root.mkdir()
    runtime.spool.mkdir()
    generation = "a" * 64
    client = FakeClient()
    client.runtime = runtime
    server = McpServer(client, workspace=tmp_path, profile="verification")
    submitted = server.handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "submit_candidate", "arguments": {
            "source": "module top; endmodule", "module": "top",
            "language": "systemverilog", "source_generation": generation,
        }},
    })
    candidate = json.loads(submitted["result"]["content"][0]["text"])["candidate_sha256"]

    def fake_runner(arguments, *, client, workspace):
        assert arguments == {"reference": "x", "through": "snapshot"}
        return {"ok": True, "aivw": {"status": "PASS", "run_id": "r1", "details": {
            "gate_results": {"snapshot": {"status": "PASS", "outputs": {
                "source_generation": generation,
            }}}
        }}}

    monkeypatch.setattr(mcp_aivw_handler, "run_aivw_recipe", fake_runner)
    response = server.handle({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "run_aivw_recipe", "arguments": {
            "reference": "x", "through": "snapshot", "candidate_sha256": candidate,
        }},
    })
    assert response["result"]["isError"] is False
    detail = json.loads(response["result"]["content"][0]["text"])
    assert detail["candidate_feedback"]["feedback"]["evidence"]["source_generation"] == generation


def test_tools_call_routes_and_preserves_tool_error() -> None:
    client = FakeClient()
    server = McpServer(client)
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": "call-1",
            "method": "tools/call",
            "params": {"name": "eval_skill", "arguments": {"code": "1+2"}},
        }
    )
    assert client.calls == [("eval_skill", {"code": "1+2"})]
    assert response["result"]["isError"] is False

    failed = McpServer(FakeClient(ok=False)).handle(
        {
            "jsonrpc": "2.0",
            "id": "call-2",
            "method": "tools/call",
            "params": {"name": "load_skill_file", "arguments": {"path": "bad.il"}},
        }
    )
    assert failed["result"]["isError"] is True
    assert "rejected" in failed["result"]["content"][0]["text"]


def test_initialized_notification_has_no_response() -> None:
    server = McpServer(FakeClient())
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_config_binding_inspection_uses_the_source_bound_read_only_skill() -> None:
    class ConfigClient(FakeClient):
        def call(self, method: str, arguments: dict[str, Any]):
            self.calls.append((method, arguments))
            return True, {"ok": True, "value": json.dumps({"ok": True, "complete": False})}

    client = ConfigClient()
    response = McpServer(client).handle(
        {
            "jsonrpc": "2.0",
            "id": "config-1",
            "method": "tools/call",
            "params": {
                "name": "inspect_config_binding",
                "arguments": {"lib": "amsLDO", "cell": "LDO_MASTER", "view": "config"},
            },
        }
    )
    assert response["result"]["isError"] is False
    assert client.calls == [
        (
            "eval_skill",
            {"code": 'aiInspectConfigBinding("amsLDO" "LDO_MASTER" "config" 50)'},
        )
    ]
    assert json.loads(response["result"]["content"][0]["text"]) == {
        "ok": True,
        "complete": False,
    }


def test_oversized_stdio_line_returns_jsonrpc_error(monkeypatch) -> None:
    monkeypatch.setattr(mcp_module, "MAX_LINE_BYTES", 32)
    reader = io.BytesIO(b"x" * 40 + b"\n")
    writer = io.BytesIO()
    assert McpServer(FakeClient(), reader=reader, writer=writer).run() == 0
    response = decode_line(writer.getvalue().rstrip(b"\n"))
    assert response["error"]["code"] == -32700


def test_eval_limit_is_enforced_in_utf8_bytes(tmp_path) -> None:
    root = tmp_path / "runtime"
    spool = root / "spool"
    spool.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    runtime = RuntimePaths(root, root / "x", spool)
    client = SocketClient(root / "missing.sock", "secret", 1.0, runtime)
    ok, error = client.call("eval_skill", {"code": "电" * (MAX_EVAL_BYTES // 3 + 1)})
    assert ok is False
    assert error["code"] == "payload_too_large"
    assert "shorten or split" in error["message"]


def test_large_eval_source_spool_is_removed_after_success(tmp_path, monkeypatch) -> None:
    root = tmp_path / "runtime"
    spool = root / "spool"
    spool.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    runtime = RuntimePaths(root, root / "x", spool)
    request_id = "a" * 32
    client = SocketClient(runtime.socket, "secret", 1.0, runtime)
    client._socket = SimpleNamespace(sendall=lambda _data: None)
    client._reader = io.BytesIO(
        encode_line({"id": request_id, "ok": True, "result": {"value": "nil"}})
    )
    monkeypatch.setattr(mcp_transport.uuid, "uuid4", lambda: SimpleNamespace(hex=request_id))

    ok, _result = client.call("eval_skill", {"code": "x" * 32_769})

    assert ok is True
    assert list(spool.iterdir()) == []


def test_large_eval_source_spool_is_removed_after_connect_failure(tmp_path) -> None:
    root = tmp_path / "runtime"
    spool = root / "spool"
    spool.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    runtime = RuntimePaths(root, root / "missing.sock", spool)
    client = SocketClient(runtime.socket, "secret", 1.0, runtime)

    ok, error = client.call("eval_skill", {"code": "x" * 32_769})

    assert ok is False
    assert error["code"] == "bridge_unavailable"
    assert "before request submission; no request was sent" in error["message"]
    assert list(spool.iterdir()) == []
