from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

import cadai.controller as controller_module
from cadai.launch import assistant_instructions, build_claude_command, build_claude_mcp_config
from cadai.mcp import TOOLS
from cadai.runtime import RuntimePaths


def _runtime(tmp_path: Path) -> RuntimePaths:
    root = tmp_path / "cad-ai-claude"
    spool = root / "spool"
    spool.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    spool.chmod(0o700)
    return RuntimePaths(root, root / "bridge.sock", spool)


def test_claude_mcp_config_scopes_bridge_credentials_to_server(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    entry = tmp_path / "python" / "sico-ai"
    payload = json.loads(
        build_claude_mcp_config(
            "/opt/python/bin/python3",
            entry,
            runtime,
            "private-session-token",
            300,
            environment={"SICO_AI_PYTHON_LIBRARY_PATH": "/opt/python/lib"},
        )
    )
    server = payload["mcpServers"]["sico_ic"]
    assert server["type"] == "stdio"
    assert server["command"] == "/opt/python/bin/python3"
    assert server["args"] == ["-s", str(entry), "mcp", "--request-timeout", "305"]
    assert server["timeout"] == 310_000
    assert server["env"] == {
        "SICO_HOME": str(Path(__file__).resolve().parents[4]),
        "SICO_AI_RUNTIME": str(runtime.root),
        "SICO_AI_SOCKET": str(runtime.socket),
        "SICO_AI_SPOOL": str(runtime.spool),
        "SICO_AI_TOKEN": "private-session-token",
        "PYTHONDONTWRITEBYTECODE": "1",
        "LD_LIBRARY_PATH": "/opt/python/lib",
    }


def test_claude_mcp_config_keeps_sico_temp_environment(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    temporary = tmp_path / "launch" / ".sico"
    temporary.mkdir(parents=True, mode=0o700)
    temporary.parent.chmod(0o700)

    payload = json.loads(
        build_claude_mcp_config(
            "/opt/python/bin/python3",
            tmp_path / "python" / "sico-ai",
            runtime,
            "private-session-token",
            300,
            environment={"SICO_TEMP_DIR": str(temporary)},
        )
    )

    environment = payload["mcpServers"]["sico_ic"]["env"]
    assert environment["SICO_TEMP_DIR"] == str(temporary)
    assert all(
        environment[name] == str(temporary / "ai")
        for name in ("TMPDIR", "TMP", "TEMP", "SQLITE_TMPDIR")
    )
    assert environment["XDG_CACHE_HOME"] == str(temporary / "ai/cache")
    assert environment["XDG_RUNTIME_DIR"] == str(temporary / "ai/runtime")


def test_claude_mcp_config_passes_explicit_skill_reference_root(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    reference_root = tmp_path / "offline-skill-reference"

    payload = json.loads(
        build_claude_mcp_config(
            "/opt/python/bin/python3",
            tmp_path / "python" / "sico-ai",
            runtime,
            "private-session-token",
            300,
            environment={"SICO_AI_SKILL_REFERENCE_ROOT": str(reference_root)},
        )
    )

    assert payload["mcpServers"]["sico_ic"]["env"]["SICO_AI_SKILL_REFERENCE_ROOT"] == str(
        reference_root
    )


def test_claude_mcp_defers_optional_reference_validation(tmp_path: Path) -> None:
    payload = json.loads(build_claude_mcp_config(
        "/opt/python/bin/python3", tmp_path / "python/sico-ai", _runtime(tmp_path),
        "private-session-token", 300,
        environment={"SICO_AI_SKILL_REFERENCE_ROOT": "relative/reference"}))
    assert payload["mcpServers"]["sico_ic"]["env"]["SICO_AI_SKILL_REFERENCE_ROOT"] == "relative/reference"


def test_claude_command_allows_every_session_mcp_tool(tmp_path: Path) -> None:
    config = tmp_path / "private-mcp-config"
    command = build_claude_command(
        "/bin/claude",
        config,
        environment={"SICO_CLAUDE_MODEL": "claude-sonnet-test"},
    )
    expected_prefix = [
        "/bin/claude",
        "--mcp-config",
        str(config),
        "--strict-mcp-config",
        "--permission-mode",
        "default",
        "--no-chrome",
        "--allowedTools="
        + ",".join(f"mcp__sico_ic__{tool['name']}" for tool in TOOLS),
        "--name",
        "Virtuoso",
        "--append-system-prompt",
        assistant_instructions(),
        "--model",
        "claude-sonnet-test",
    ]
    assert command == expected_prefix
    assert "private-session-token" not in " ".join(command)


def test_claude_session_launches_in_generic_terminal_with_private_mcp_config(
    tmp_path: Path, monkeypatch
) -> None:
    runtime = _runtime(tmp_path)
    captured: dict[str, Any] = {"events": []}

    class SessionTransport:
        def start(self) -> None:
            pass

        def send(self, payload: dict[str, Any]) -> None:
            captured["events"].append(payload)

        def close(self) -> None:
            pass

    class SessionServer:
        def __init__(self, *_args, **_kwargs):
            pass

        def serve_forever(self) -> None:
            pass

        def wait_idle(self, _timeout: float) -> bool:
            return True

        def quiesce(self) -> None:
            pass

        def shutdown(self) -> None:
            pass

        def server_close(self) -> None:
            pass

    class SessionProcess:
        def __init__(self, command, **kwargs):
            captured["command"] = list(command)
            captured["environment"] = dict(kwargs["env"])
            control_fd = int(kwargs["env"]["SICO_AI_CONTROL_FD"])
            os.fstat(control_fd)
            captured["pass_fds"] = kwargs["pass_fds"]
            captured["control_fd"] = control_fd
            config_path = Path(command[command.index("--mcp-config") + 1])
            captured["config_mode"] = config_path.stat().st_mode & 0o777
            captured["config"] = json.loads(config_path.read_text(encoding="utf-8"))

        def wait(self, timeout=None) -> int:
            return 0

        def poll(self) -> int:
            return 0

    monkeypatch.setattr(
        controller_module.RuntimePaths, "create", lambda **_kwargs: runtime
    )
    monkeypatch.setattr(controller_module, "VirtuosoTransport", SessionTransport)
    monkeypatch.setattr(controller_module, "UnixRequestServer", SessionServer)
    monkeypatch.setattr(controller_module.subprocess, "Popen", SessionProcess)
    monkeypatch.setattr(
        "cadai.terminal_environment._locale_supports_utf8",
        lambda value, _environment: value == "C.UTF-8",
    )
    monkeypatch.delenv("CAD_HOME", raising=False)
    monkeypatch.setenv("SICO_PYTHON_ROOT", "/software/pkgs/python/3.9.13")
    monkeypatch.setenv("SICO_PYTHON", "/software/pkgs/python/3.9.13/bin/python3")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SICO_TEMP_DIR", raising=False)
    monkeypatch.delenv("SICO_TEMP_DIR", raising=False)
    monkeypatch.setenv("SICO_CODEX_API_KEY", "codex-only-secret")
    monkeypatch.setenv("SICO_CLAUDE_MODEL", "claude-test-model")

    def fail_codex_home() -> Path:
        raise AssertionError("Claude must not create a Codex home")

    monkeypatch.setattr(controller_module, "private_codex_home", fail_codex_home)
    assert (
        controller_module.run_session(
            tmp_path,
            "/bin/true",
            None,
            300,
            agent="claude",
            claude="/bin/true",
        )
        == 0
    )

    command = captured["command"]
    assert command[:4] == [
        str(Path("/bin/true").resolve()),
        "--working-directory",
        str(tmp_path),
        "--",
    ]
    assert "--strict-mcp-config" in command
    assert command[command.index("--model") + 1] == "claude-test-model"
    assert captured["config_mode"] == 0o600
    server = captured["config"]["mcpServers"]["sico_ic"]
    assert server["command"] == "/software/pkgs/python/3.9.13/bin/python3"
    assert server["env"]["SICO_AI_RUNTIME"] == str(runtime.root)
    assert server["env"]["SICO_AI_SOCKET"] == str(runtime.socket)
    assert server["env"]["SICO_AI_SPOOL"] == str(runtime.spool)
    assert server["env"]["SICO_AI_TOKEN"]
    assert server["env"]["SICO_AI_WORKSPACE"] == str(tmp_path)
    environment = captured["environment"]
    assert environment["SICO_AI_CONTROL_FD"] == str(captured["control_fd"])
    assert environment["SICO_PYTHON"] == "/software/pkgs/python/3.9.13/bin/python3"
    assert environment["PATH"].split(os.pathsep)[0] == "/software/pkgs/python/3.9.13/bin"
    assert captured["pass_fds"] == (captured["control_fd"],)
    assert "SICO_CODEX_API_KEY" not in environment
    assert "SICO_AI_SPOOL" not in environment
    assert "SICO_AI_TOKEN" not in environment
    assert captured["events"][:2] == [
        {
            "event": "session.ready",
            "workspace": str(tmp_path),
            "agent": "claude",
            "control_protocol": "control.v2",
        },
        {"event": "session.exit", "status": 0},
    ]
    # Controller teardown stops the live-model worker after the terminal exits;
    # that internal status event is not part of the terminal lifecycle contract.
    assert all(event.get("event") != "live_model.status" for event in captured["events"][2:])
    assert not runtime.root.exists()
