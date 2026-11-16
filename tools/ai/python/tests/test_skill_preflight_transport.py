from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import pytest
from test_controller import FakeTransport
from test_controller import runtime as runtime_fixture
from test_mcp import FakeClient

from cadai.controller import RequestDispatcher
from cadai.launch import build_claude_command, build_codex_command
from cadai.mcp import McpServer, SocketClient
from cadai.runtime import write_spool
from cadai.socket_server import RequestFailure, UnixRequestServer

runtime = runtime_fixture


def call(server, name, arguments):
    return server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )


@pytest.mark.parametrize("method", ["eval_skill", "eval_skill_native", "load_skill_file"])
@pytest.mark.parametrize(
    "source",
    [
        b"progn(list(1)",
        b"list(1]",
        b"defun(foo (x) x)",
        b"(defun foo (x) x)",
        b'schCreateWire(cv "route" "flight" list(0:0 1:1) 0.0625 0.0625 0)',
        b"/* unfinished",
        b'"unfinished',
        b"nil\n(\n",
        b"nil\x00",
        b"\xff",
    ],
)
def test_rejected_source_never_reaches_virtuoso(runtime, tmp_path, method, source):
    transport = FakeTransport()
    dispatcher = RequestDispatcher(transport, runtime, tmp_path)
    if method == "load_skill_file":
        path = tmp_path / "source.ils"
        path.write_bytes(source)
        params = {"path": path.name}
    else:
        ref = write_spool(runtime.spool, "source", source)
        params = {"source_spool": {k: ref[k] for k in ("name", "size", "sha256")}}
    with pytest.raises(RequestFailure) as caught:
        dispatcher("a" * 32, method, params)
    error = caught.value.as_error()
    assert error["code"] == "skill_preflight_failed"
    assert error["data"]["phase"] == "preflight"
    assert error["data"]["executed"] is False
    assert error["data"]["source_sha256"] == hashlib.sha256(source).hexdigest()
    assert transport.requests == []
    assert dispatcher.runtime_cleanup_safe
    assert list(runtime.spool.iterdir()) == []
    assert list(runtime.virtuoso_spool.iterdir()) == []


@pytest.mark.parametrize("method", ["eval_skill", "eval_skill_native"])
def test_inline_and_spooled_code_use_same_gate(runtime, tmp_path, method):
    dispatcher = RequestDispatcher(FakeTransport(), runtime, tmp_path)
    source = ";" + "x" * 40_000 + "\ndefun(foo (x) x)"
    with pytest.raises(RequestFailure) as caught:
        dispatcher("b" * 32, method, {"code": source})
    assert caught.value.data["errors"][0]["line"] == 2


def test_materialized_source_is_exactly_the_checked_copy(runtime, tmp_path):
    path = tmp_path / "source.ils"
    source = b"; comment\r\nprocedure(foo(x) x)\r\n"
    path.write_bytes(source)

    class EditingTransport(FakeTransport):
        def request(self, payload, timeout):
            path.write_text("defun(foo (x) x)")
            return super().request(payload, timeout)

    transport = EditingTransport()
    dispatcher = RequestDispatcher(transport, runtime, tmp_path)
    dispatcher("c" * 32, "load_skill_file", {"path": path.name})
    assert transport.sources == [source]
    assert Path(transport.requests[0]["source_path"]).suffix == ".ils"


@pytest.mark.parametrize(
    "arguments",
    [
        {"code": "list(1)"},
        {"code": "lambda((x) x + 1)"},
        {"code": "(lambda (x) x + 1)"},
        {"code": "defun(foo (x) x)"},
        {"path": "source.il"},
    ],
)
def test_check_skill_is_local_and_has_no_execution_side_effects(tmp_path, arguments):
    client = FakeClient()
    path = tmp_path / "source.il"
    source = b"procedure(foo() t)"
    path.write_bytes(source)
    response = call(McpServer(client, workspace=tmp_path), "check_skill", arguments)
    report = json.loads(response["result"]["content"][0]["text"])
    assert report["executed"] is False
    assert response["result"]["isError"] is (not report["ok"])
    assert report["checks"]["native_syntax"] == "not_run"
    assert client.calls == []
    assert path.read_bytes() == source
    assert sorted(p.name for p in tmp_path.iterdir()) == ["source.il"]


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"code": "nil", "path": "x.il"},
        {"code": 1},
        {"path": False},
        {"code": ""},
        {"path": " "},
        {"code": "nil", "fix": True},
    ],
)
def test_check_tool_rejects_invalid_arguments(arguments):
    response = call(McpServer(FakeClient()), "check_skill", arguments)
    assert response["error"]["code"] == -32602


def test_preflight_diagnostics_survive_real_socket_and_mcp(runtime, tmp_path):
    transport = FakeTransport()
    dispatcher = RequestDispatcher(transport, runtime, tmp_path)
    bridge = UnixRequestServer(runtime.socket, "test-secret", dispatcher)
    worker = threading.Thread(target=bridge.serve_forever, daemon=True)
    worker.start()
    client = SocketClient(runtime.socket, "test-secret", 2.0, runtime)
    mcp = McpServer(client, workspace=tmp_path)
    try:
        # >32 KiB exercises SocketClient's inbound-spool path as well.
        code = ";" + "x" * 40_000 + "\nwhen(t list(1]"
        response = call(mcp, "eval_skill", {"code": code})
        assert response["result"]["isError"] is True
        error = json.loads(response["result"]["content"][0]["text"])
        assert error["code"] == "skill_preflight_failed"
        assert error["data"]["executed"] is False
        assert error["data"]["errors"][0]["code"] == "super_right_bracket"
        assert error["data"]["errors"][0]["opening"]["line"] == 2
        assert transport.requests == []
        assert list(runtime.spool.iterdir()) == []
        assert list(runtime.virtuoso_spool.iterdir()) == []
        repaired = call(mcp, "eval_skill", {"code": "when(t list(1))"})
        assert repaired["result"]["isError"] is False
        assert len(transport.requests) == 1
    finally:
        client.close()
        bridge.shutdown()
        bridge.server_close()
        worker.join(timeout=2)


@pytest.mark.parametrize("succeed", [True, False])
def test_captured_output_survives_controller_socket_and_mcp(runtime, tmp_path, succeed):
    payload = {
        "ok": succeed,
        "output": "printed\n*WARNING* warning\n" + ("" if succeed else "*Error* failed\n"),
        "output_truncated": False,
        "output_capture": "skill_ports",
        **({"value": "nil", "value_truncated": False} if succeed
           else {"error": "failed", "error_truncated": False}),
    }

    class EvalTransport(FakeTransport):
        def request(self, request, timeout):
            super().request(request, timeout)
            return succeed, {"message": "SKILL execution failed"} if not succeed else {}

    transport = EvalTransport(payload)
    bridge = UnixRequestServer(
        runtime.socket, "test-secret", RequestDispatcher(transport, runtime, tmp_path)
    )
    worker = threading.Thread(target=bridge.serve_forever, daemon=True)
    worker.start()
    client = SocketClient(runtime.socket, "test-secret", 2.0, runtime)
    try:
        response = call(McpServer(client, workspace=tmp_path), "eval_skill", {"code": "nil"})
        assert response["result"]["isError"] is not succeed
        detail = json.loads(response["result"]["content"][0]["text"])
        if succeed:
            assert detail == payload
        else:
            assert detail["code"] == "skill_error" and detail["data"] == payload
        assert len(transport.requests) == 1
        assert list(runtime.spool.iterdir()) == []
        assert list(runtime.virtuoso_spool.iterdir()) == []
    finally:
        client.close()
        bridge.shutdown()
        bridge.server_close()
        worker.join(timeout=2)


def test_check_is_advertised_read_only():
    normal = McpServer(FakeClient())
    tools = normal.handle({"id": 1, "method": "tools/list"})["result"]["tools"]
    tool = next(t for t in tools if t["name"] == "check_skill")
    assert tool["annotations"] == {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }


def test_both_clients_receive_skill_preflight_instructions(tmp_path):
    codex = build_codex_command(
        "/bin/codex", "/bin/python3", tmp_path, tmp_path, 300, environment={}
    )
    codex_prompt = json.loads(
        next(item.split("=", 1)[1] for item in codex if item.startswith("developer_instructions="))
    )
    claude = build_claude_command("/bin/claude", tmp_path / "mcp.json", environment={})
    claude_prompt = claude[claude.index("--append-system-prompt") + 1]
    for prompt in (codex_prompt, claude_prompt):
        for required in ("check_skill", "procedure(name(args)", "lambda(", "prog(",
                         "let(", "defun", "nlambda", "executed=false"):
            assert required in prompt
