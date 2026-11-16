from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import zipfile
from pathlib import Path
from typing import Any, BinaryIO

import pytest

from cadai.launch import build_codex_command
from cadai.mcp import TOOLS
from cadai.protocol import decode_line, encode_line
from cadai.runtime import RuntimePaths
from cadai.socket_server import UnixRequestServer


def _runtime(tmp_path: Path) -> RuntimePaths:
    root = tmp_path / "cad-ai-integration"
    spool = root / "spool"
    spool.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    spool.chmod(0o700)
    return RuntimePaths(root, root / "bridge.sock", spool)


def _exchange(writer: BinaryIO, reader: BinaryIO, message: dict[str, Any]) -> dict[str, Any]:
    writer.write(encode_line(message))
    writer.flush()
    raw = reader.readline()
    assert raw.endswith(b"\n")
    return decode_line(raw[:-1])


def test_mcp_subprocess_initialize_list_and_call(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    calls: list[tuple[str, dict[str, Any]]] = []

    def dispatch(_request_id: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
        calls.append((method, params))
        return {"ok": True, "cwd": "/fake-virtuoso"}

    server = UnixRequestServer(runtime.socket, "integration-secret", dispatch)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = dict(os.environ)
    env.update(
        {
            "SICO_AI_RUNTIME": str(runtime.root),
            "SICO_AI_SOCKET": str(runtime.socket),
            "SICO_AI_SPOOL": str(runtime.spool),
            "SICO_AI_TOKEN": "integration-secret",
        }
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "cadai", "mcp", "--request-timeout", "2"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    try:
        initialized = _exchange(
            process.stdin,
            process.stdout,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            },
        )
        assert initialized["result"]["protocolVersion"] == "2025-06-18"
        process.stdin.write(encode_line({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        process.stdin.flush()
        listed = _exchange(
            process.stdin,
            process.stdout,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert [tool["name"] for tool in listed["result"]["tools"]] == [
            tool["name"] for tool in TOOLS
        ]
        called = _exchange(
            process.stdin,
            process.stdout,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "get_context", "arguments": {}},
            },
        )
        assert called["result"]["isError"] is False
        payload = json.loads(called["result"]["content"][0]["text"])
        assert payload == {"cwd": "/fake-virtuoso", "ok": True}
        searched = _exchange(
            process.stdin,
            process.stdout,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "search_skill_api", "arguments": {"query": "substring"}},
            },
        )
        search_payload = json.loads(searched["result"]["content"][0]["text"])
        assert searched["result"]["isError"] is False
        assert search_payload["matches"][0]["name"] == "substring"
        retrieved = _exchange(
            process.stdin,
            process.stdout,
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "get_skill_api", "arguments": {"name": "substring"}},
            },
        )
        get_payload = json.loads(retrieved["result"]["content"][0]["text"])
        assert retrieved["result"]["isError"] is False
        assert get_payload["matches"][0]["name"] == "substring"
        assert calls == [
            ("eval_skill_native", {"code": "aiEntryContext(nil 8 nil)"}),
            ("get_context", {}),
        ]
    finally:
        process.stdin.close()
        assert process.wait(timeout=5) == 0
        assert process.stderr is not None
        assert process.stderr.read() == b""
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_wheel_mcp_working_directory_exists_and_launches_module(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[1]
    wheel_source = tmp_path / "wheel-source"
    wheel_source.mkdir()
    shutil.copy2(project / "pyproject.toml", wheel_source)
    shutil.copytree(
        project / "cadai",
        wheel_source / "cadai",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-build-isolation",
            "--no-deps",
            "--wheel-dir",
            str(wheel_dir),
            str(wheel_source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    site_packages = tmp_path / "site-packages"
    with zipfile.ZipFile(next(wheel_dir.glob("cadai-*.whl"))) as archive:
        archive.extractall(site_packages)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(site_packages)
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import subprocess, sys\n"
                "from cadai.controller import _session_roots\n"
                "python_root, _install_root = _session_roots()\n"
                "assert python_root.is_dir()\n"
                "result = subprocess.run([sys.executable, '-m', 'cadai', '--help'], "
                "cwd=python_root, capture_output=True, text=True)\n"
                "assert result.returncode == 0, result.stderr\n"
            ),
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stderr


def test_installed_supported_codex_parses_bridge_and_auto_approval_config(
    tmp_path: Path,
) -> None:
    codex = os.environ.get("CAD_TEST_CODEX_CLI") or shutil.which("codex")
    if codex is None:
        pytest.skip("codex CLI is not installed")
    version = subprocess.run(
        [codex, "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert version == "codex-cli 0.162.1"

    codex_home = tmp_path / "codex-home"
    codex_home.mkdir(mode=0o700)
    env = dict(os.environ)
    env["CODEX_HOME"] = str(codex_home)
    command = build_codex_command(
        codex,
        sys.executable,
        tmp_path,
        Path(__file__).resolve().parents[1],
        300,
        environment={},
    )
    result = subprocess.run(
        [*command, "mcp", "get", "sico_ic"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    expected_tools = ", ".join(tool["name"] for tool in TOOLS)
    assert f"enabled_tools: {expected_tools}" in result.stdout
    assert not any("approval_mode=" in item for item in command)
    assert "tool_timeout_sec: 310" in result.stdout
    assert str(Path(__file__).resolve().parents[1] / "sico-ai") in result.stdout
    assert "mcp --request-timeout 305" in result.stdout

    invalid = [
        'mcp_servers.sico_ic.tool_timeout_sec="invalid"'
        if item == 'mcp_servers.sico_ic.tool_timeout_sec=310'
        else item
        for item in command
    ]
    rejected = subprocess.run(
        [*invalid, "mcp", "get", "sico_ic"],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode != 0
    assert "mcp_servers.sico_ic.tool_timeout_sec" in rejected.stderr

    assert "use_linux_sandbox_bwrap" not in command
