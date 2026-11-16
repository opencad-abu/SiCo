from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from cadai.launch import build_codex_command, prepare_codex_gateway, resolve_agent_executable
from cadai.runtime import RuntimePaths
from cadai.socket_server import UnixRequestServer


def _sse(events: list[dict[str, Any]]) -> bytes:
    chunks = []
    for event in events:
        encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        chunks.append(f"event: {event['type']}\ndata: {encoded}\n\n")
    return "".join(chunks).encode()


def _completed(response_id: str) -> dict[str, Any]:
    return {
        "type": "response.completed",
        "response": {
            "id": response_id,
            "usage": {
                "input_tokens": 0,
                "input_tokens_details": None,
                "output_tokens": 0,
                "output_tokens_details": None,
                "total_tokens": 0,
            },
        },
    }


class _ResponsesServer(ThreadingHTTPServer):
    def __init__(self, responses: list[bytes]):
        super().__init__(("127.0.0.1", 0), _ResponsesHandler)
        self.responses: queue.Queue[bytes] = queue.Queue()
        for response in responses:
            self.responses.put(response)
        self.requests: list[bytes] = []
        self.authorizations: list[str | None] = []
        self.requests_lock = threading.Lock()


class _ResponsesHandler(BaseHTTPRequestHandler):
    server: _ResponsesServer

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        with self.server.requests_lock:
            self.server.requests.append(body)
            self.server.authorizations.append(self.headers.get("Authorization"))
        try:
            response = self.server.responses.get_nowait()
        except queue.Empty:
            self.send_error(500, "unexpected extra model request")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, _format: str, *args: object) -> None:
        del args


def _runtime(tmp_path: Path) -> RuntimePaths:
    root = tmp_path / "cad-ai-e2e"
    spool = root / "spool"
    spool.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    spool.chmod(0o700)
    return RuntimePaths(root, root / "bridge.sock", spool)


@pytest.mark.parametrize("compatibility", [False, True], ids=["native", "wrapper"])
def test_supported_codex_drives_real_mcp_handshake_and_tool_call(
    tmp_path: Path, compatibility: bool
) -> None:
    codex = os.environ.get("CAD_TEST_CODEX_CLI") or shutil.which("codex")
    if codex is None:
        pytest.skip("Set CAD_TEST_CODEX_CLI to original Codex 0.162.1")
    codex = resolve_agent_executable("codex", codex, environment={})
    version = subprocess.run(
        [codex, "--version"], check=True, capture_output=True, text=True
    ).stdout.strip()
    assert version == "codex-cli 0.162.1"
    call_id = "cad-ai-e2e-call"
    first = _sse(
        [
            {"type": "response.created", "response": {"id": "resp-1"}},
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "function_call",
                    "call_id": call_id,
                    **(
                        {"name": "mcp__sico_ic__get_context"} if compatibility
                        else {"namespace": "mcp__sico_ic", "name": "get_context"}
                    ),
                    "arguments": "{}",
                },
            },
            _completed("resp-1"),
        ]
    )
    second = _sse(
        [
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "message",
                    "role": "assistant",
                    "id": "msg-1",
                    "content": [{"type": "output_text", "text": "CAD Codex E2E OK"}],
                },
            },
            _completed("resp-2"),
        ]
    )
    model_server = _ResponsesServer([first, second])
    model_thread = threading.Thread(target=model_server.serve_forever, daemon=True)
    model_thread.start()

    runtime = _runtime(tmp_path)
    bridge_calls: list[tuple[str, dict[str, Any]]] = []

    def dispatch(_request_id: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
        bridge_calls.append((method, params))
        return {"ok": True, "cwd": "/fake-virtuoso"}

    bridge = UnixRequestServer(runtime.socket, "e2e-secret", dispatch)
    bridge_thread = threading.Thread(target=bridge.serve_forever, daemon=True)
    bridge_thread.start()

    codex_home = tmp_path / "codex-home"
    codex_home.mkdir(mode=0o700)
    python_root = Path(__file__).resolve().parents[1]
    base_url = f"http://127.0.0.1:{model_server.server_port}/v1"
    model = "cad-ai-e2e-model"
    model_environment = {
        "SICO_MODEL": model,
        "SICO_API_BASE_URL": base_url,
        "SICO_API_KEY": "e2e-api-secret",
        "SICO_LEGACY_0114": "1" if compatibility else "0",
    }
    wrapper = prepare_codex_gateway(model_environment)
    command = build_codex_command(
        codex,
        sys.executable,
        tmp_path,
        python_root,
        10,
        environment=model_environment,
    )
    assert "e2e-api-secret" not in " ".join(command)
    assert "use_linux_sandbox_bwrap" not in command
    command.extend(
        [
            "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "Call the Virtuoso get_context tool exactly once.",
        ]
    )
    env = dict(os.environ)
    env.update(
        {
            "SICO_AI_RUNTIME": str(runtime.root),
            "SICO_AI_SOCKET": str(runtime.socket),
            "SICO_AI_SPOOL": str(runtime.spool),
            "SICO_AI_TOKEN": "e2e-secret",
            "SICO_API_KEY": model_environment["SICO_API_KEY"],
            "CODEX_HOME": str(codex_home),
            "NO_PROXY": "127.0.0.1,localhost",
        }
    )
    try:
        result = subprocess.run(
            command,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert "CAD Codex E2E OK" in result.stdout
        assert bridge_calls == [
            ("eval_skill_native", {"code": "aiEntryContext(nil 8 nil)"}),
            ("get_context", {}),
        ]
        assert len(model_server.requests) == 2
        assert model_server.authorizations == ["Bearer e2e-api-secret"] * 2
        for body in model_server.requests:
            request = json.loads(body)
            if compatibility:
                assert not any(tool.get("type") == "namespace" for tool in request["tools"])
                assert any(
                    tool.get("type") == "function" and tool["name"] == "mcp__sico_ic__get_context"
                    for tool in request["tools"]
                )
            else:
                namespace = next(
                    tool for tool in request["tools"]
                    if tool.get("type") == "namespace" and tool.get("name") == "mcp__sico_ic"
                )
                assert any(tool["name"] == "get_context" for tool in namespace["tools"])
        final_request = json.loads(model_server.requests[1])
        assert final_request["model"] == model
        developer_text = json.dumps(
            [item for item in final_request["input"] if item.get("role") == "developer"],
            ensure_ascii=False,
        )
        assert "Cadence Virtuoso 内的 AI Assistant" in developer_text
        assert "SICO_PYTHON" in developer_text
        assert "SICO_VIRTUOSO_LOADEDMODULES" in developer_text
        outputs = [
            item
            for item in final_request["input"]
            if item.get("type") == "function_call_output" and item.get("call_id") == call_id
        ]
        assert len(outputs) == 1
    finally:
        if wrapper:
            wrapper.close()
        bridge.shutdown()
        bridge.server_close()
        bridge_thread.join(timeout=2)
        model_server.shutdown()
        model_server.server_close()
        model_thread.join(timeout=2)
