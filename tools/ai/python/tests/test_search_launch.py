from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from cadai.launch import (
    build_claude_command,
    build_codex_command,
    prepare_terminal_environment,
)
from cadai.protocol import decode_line, encode_line
from cadai.runtime import RuntimePaths
from cadai.search_runtime import resolve_rg
from cadai.socket_server import UnixRequestServer


@pytest.mark.parametrize("agent", ["codex", "claude"])
def test_both_managed_terminals_provide_rg(tmp_path, monkeypatch, agent):
    monkeypatch.setenv("PATH", "/no-system-rg")
    monkeypatch.delenv("CAD_AI_RELEASE_ROOT", raising=False)
    monkeypatch.delenv("CAD_TEMP_DIR", raising=False)
    monkeypatch.setenv("SICO_TEMP_DIR", str(tmp_path / ".sico"))
    runtime = RuntimePaths(tmp_path, tmp_path / "bridge.sock", tmp_path / "spool")
    environment, _ = prepare_terminal_environment(runtime, "test-token", tmp_path, None, agent)
    assert shutil.which("rg", path=environment["PATH"]) == str(resolve_rg())
    probe = subprocess.run(["rg", "--version"], env=environment, capture_output=True, text=True)
    assert probe.returncode == 0 and probe.stdout.startswith("ripgrep 15.2.0")


def test_search_is_enabled_for_both_interactive_clients():
    command = build_codex_command(
        "codex", sys.executable, Path("/work"), Path("/cad/python"), 30, environment={}
    )
    enabled = next(
        value.partition("=")[2]
        for value in command
        if value.startswith("mcp_servers.sico_ic.enabled_tools=")
    )
    assert "Search" in json.loads(enabled)
    claude = build_claude_command("claude", Path("/mcp.json"), environment={})
    assert any("mcp__sico_ic__Search" in value for value in claude)


def test_installed_mcp_search_without_system_rg_or_source_runtime(tmp_path):
    product = tmp_path / "installed"
    installed = product / "tools/ai"
    source = Path(__file__).resolve().parents[2]
    shutil.copytree(
        source / "python/cadai",
        installed / "python/cadai",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    common = product / "tools/common/python"
    shutil.copytree(source.parent / "common/python", common,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests"))
    (product / "bin").mkdir()
    (product / "etc/config").mkdir(parents=True)
    shutil.copy2(source.parents[1] / "etc/config/sico-install.json", product / "etc/config")
    shutil.copytree(source / "runtime/ripgrep", installed / "runtime/ripgrep")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "source.il").write_text("procedure(hello() t)\n")
    runtime_root = tmp_path / "runtime"
    spool = runtime_root / "spool"
    spool.mkdir(parents=True, mode=0o700)
    runtime_root.chmod(0o700)
    runtime = RuntimePaths(runtime_root, runtime_root / "bridge.sock", spool)
    calls = []

    def dispatch(_request_id, method, _params):
        calls.append(method)
        return {"ok": True}

    server = UnixRequestServer(runtime.socket, "test-token", dispatch)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = {
        "PATH": "/no-system-rg",
        "SICO_HOME": str(product),
        "PYTHONPATH": str(common),
        "PYTHONDONTWRITEBYTECODE": "1",
        "LD_LIBRARY_PATH": os.environ.get("LD_LIBRARY_PATH", ""),
        "CAD_AI_RUNTIME": str(runtime.root),
        "CAD_AI_SOCKET": str(runtime.socket),
        "CAD_AI_SPOOL": str(spool),
        "CAD_AI_TOKEN": "test-token",
        "CAD_AI_WORKSPACE": str(workspace),
    }
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "Search",
            "arguments": {"pattern": "procedure("},
        },
    }
    try:
        result = subprocess.run(
            [sys.executable, "-m", "cadai", "mcp"],
            cwd=installed / "python",
            env=environment,
            input=encode_line(request),
            capture_output=True,
            timeout=10,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert result.returncode == 0, result.stderr.decode()
    content = decode_line(result.stdout.rstrip(b"\n"))["result"]
    assert not content["isError"]
    detail = json.loads(content["content"][0]["text"])
    assert detail["complete"] and detail["returned"] == 1
    assert detail["results"][0]["path"] == "./source.il"
    assert calls == []
