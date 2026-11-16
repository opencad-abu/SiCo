from __future__ import annotations

import json
import os
import select
import signal
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest

import cadai.controller as controller_module
from assistant_installation_fixture import assistant_installation

AI_ROOT = Path(__file__).resolve().parents[2]
CAD_PYTHON = os.environ.get("CAD_PYTHON") or sys.executable


@pytest.fixture(autouse=True)
def isolated_launch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CAD_TEMP_DIR", raising=False)
    monkeypatch.delenv("SICO_TEMP_DIR", raising=False)


class _StopAfterTerminalResolution(RuntimeError):
    pass


@pytest.mark.parametrize('explicit,configured,expected', [
    ('/explicit/terminal', '/configured/terminal', '/explicit/terminal'),
    (None, '/configured/terminal', '/configured/terminal'),
    (None, None, None),
])
def test_controller_terminal_override_precedence(tmp_path, monkeypatch, explicit, configured, expected):
    calls = []

    def stop_after_resolution(value, candidates):
        calls.append((value, tuple(map(str, candidates))))
        raise _StopAfterTerminalResolution

    monkeypatch.setattr(controller_module, 'resolve_executable', stop_after_resolution)
    monkeypatch.delenv('CAD_AI_TERMINAL', raising=False)
    monkeypatch.delenv('CAD_CODEX_TERMINAL', raising=False)
    if configured is None:
        monkeypatch.delenv('SICO_AI_TERMINAL', raising=False)
    else:
        monkeypatch.setenv('SICO_AI_TERMINAL', configured)
    with pytest.raises(_StopAfterTerminalResolution):
        controller_module.run_session(tmp_path, explicit, '/bin/true', 1)
    assert calls == [(expected, (str(AI_ROOT / 'bin/sico-ai-terminal'),))]


@pytest.mark.parametrize('legacy', ['CAD_AI_TERMINAL', 'CAD_CODEX_TERMINAL'])
def test_retired_terminal_environment_is_rejected(tmp_path, monkeypatch, legacy):
    monkeypatch.delenv('SICO_AI_TERMINAL', raising=False)
    monkeypatch.setenv(legacy, '/legacy/terminal')
    with pytest.raises(ValueError, match='removed after SiCo v0.0.1'):
        controller_module.run_session(tmp_path, None, '/bin/true', 1)


def test_controller_ignores_foreign_terminal_runtime_root(
    tmp_path: Path, monkeypatch
) -> None:
    source_root = assistant_installation(tmp_path / "source", monkeypatch) / "tools/ai"
    release_root = tmp_path / "release" / "ai"
    terminal = release_root / "bin" / "sico-ai-terminal"
    terminal.parent.mkdir(parents=True)
    terminal.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    terminal.chmod(0o755)
    bundled = release_root / "runtime/codex/x86_64-unknown-linux-musl/bin/codex"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    bundled.chmod(0o755)
    captured: list[tuple[str, str | None, tuple[Path, ...]]] = []

    def session_roots() -> tuple[Path, Path]:
        return source_root / "python", source_root

    class _StopAfterAgentResolution(RuntimeError):
        pass

    def resolve_agent(agent, explicit, *, install_roots):
        captured.append((agent, explicit, install_roots))
        raise _StopAfterAgentResolution

    monkeypatch.setattr(controller_module, "_session_roots", session_roots)
    monkeypatch.setattr(controller_module, "resolve_agent_executable", resolve_agent)
    monkeypatch.setenv("SICO_AI_TERMINAL", str(terminal))
    monkeypatch.delenv("SICO_AI_RELEASE_ROOT", raising=False)

    with pytest.raises(_StopAfterAgentResolution):
        controller_module.run_session(tmp_path, None, None, 1)

    assert captured == [("codex", None, (source_root.resolve(),))]


def test_controller_does_not_infer_install_root_from_arbitrary_terminal(
    tmp_path: Path, monkeypatch
) -> None:
    source_root = assistant_installation(tmp_path / "source", monkeypatch) / "tools/ai"
    terminal = tmp_path / "bin" / "custom-terminal"
    terminal.parent.mkdir()
    terminal.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    terminal.chmod(0o755)
    captured: list[tuple[Path, ...]] = []

    def session_roots() -> tuple[Path, Path]:
        return source_root / "python", source_root

    class _StopAfterAgentResolution(RuntimeError):
        pass

    def resolve_agent(agent, explicit, *, install_roots):
        captured.append(install_roots)
        raise _StopAfterAgentResolution

    monkeypatch.setattr(controller_module, "_session_roots", session_roots)
    monkeypatch.setattr(controller_module, "resolve_agent_executable", resolve_agent)
    monkeypatch.setenv("SICO_AI_TERMINAL", str(terminal))
    monkeypatch.delenv("SICO_AI_RELEASE_ROOT", raising=False)
    with pytest.raises(_StopAfterAgentResolution):
        controller_module.run_session(tmp_path, None, None, 1)

    assert captured == [(source_root.resolve(),)]


def _write_contract_terminal(path: Path, log: Path) -> None:
    path.write_text(
        "\n".join(
            (
                f"#!{CAD_PYTHON}",
                "import json",
                "import os",
                "import sys",
                "from pathlib import Path",
                "control_fd = int(os.environ.pop('SICO_AI_CONTROL_FD'))",
                "separator = sys.argv.index('--')",
                "workspace_index = sys.argv.index('--working-directory') + 1",
                "with os.fdopen(control_fd, 'rb', buffering=0) as control:",
                "    log_path = Path(os.environ['FAKE_TERMINAL_LOG'])",
                "    with log_path.open('w', encoding='utf-8') as output:",
                "        output.write(json.dumps({",
                "            'cwd': os.getcwd(),",
                "            'workspace': sys.argv[workspace_index],",
                "            'agent': sys.argv[separator + 1:],",
                "            'model_url': os.environ.get('SICO_API_BASE_URL'),",
                "            'has_real_key': "
                "os.environ.get('SICO_API_KEY') == 'test-gateway-key',",
                "            'control_fd': control.fileno() >= 0,",
                "        }) + '\\n')",
                "        output.flush()",
                "        for raw in control:",
                "            command = raw.decode('ascii').strip()",
                "            output.write(json.dumps({'control': command}) + '\\n')",
                "            output.flush()",
                "            if command == 'shutdown':",
                "                break",
                "",
            )
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)


def _read_event(process: subprocess.Popen[bytes]) -> dict[str, object]:
    assert process.stdout is not None
    ready, _writable, _exceptional = select.select([process.stdout], [], [], 5)
    if not ready:
        raise AssertionError("controller did not emit session.ready")
    raw = process.stdout.readline()
    if not raw:
        raise AssertionError("controller stdout closed before session.ready: " +
                             process.stderr.read().decode("utf-8", "replace"))
    return json.loads(raw)


@pytest.mark.parametrize("runtime_source", ("explicit", "terminal", "symlink", "configured"))
@pytest.mark.parametrize("wrapper", [False, True], ids=["native", "wrapper"])
def test_controller_can_launch_terminal_from_sico_ai_terminal(
    tmp_path: Path, runtime_source: str, codex_stub: Path, wrapper: bool,
) -> None:
    product = tmp_path / "release"
    release_root = product / "tools/ai"
    repo = AI_ROOT.parents[1]
    for relative in ("tools/ai/python", "tools/common/python", "tools/sico/python", "tools/ai/skills", "tools/ai/runtime/ripgrep"):
        shutil.copytree(repo / relative, product / relative,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests"))
    (product / "bin").mkdir()
    (product / "etc/config").mkdir(parents=True)
    shutil.copy2(repo / "etc/config/sico-install.json", product / "etc/config")
    if runtime_source in {"terminal", "symlink"}:
        terminal = release_root / "bin" / "sico-ai-terminal"
    else:
        terminal = tmp_path / "bin" / "custom-terminal"
    terminal.parent.mkdir(parents=True)
    log = tmp_path / "terminal-probe.jsonl"
    _write_contract_terminal(terminal, log)
    if runtime_source == "symlink":
        site_entry = tmp_path / "site-terminal"
        site_entry.symlink_to(terminal)
        terminal = site_entry
    bundled = release_root / "runtime/codex/x86_64-unknown-linux-musl/bin/codex"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(codex_stub.read_bytes())
    bundled.chmod(0o755)
    runtime_home = tmp_path / "xdg-runtime"
    runtime_home.mkdir(mode=0o700)

    environment = os.environ.copy()
    environment.update(
        {
            "SICO_HOME": str(product),
            "SICO_PYTHON": CAD_PYTHON,
            "SICO_AI_TERMINAL": str(terminal),
            "SICO_CODEX_HOME": str(tmp_path / "codex-home"),
            "FAKE_TERMINAL_LOG": str(log),
            "PYTHONDONTWRITEBYTECODE": "1",
            "XDG_RUNTIME_DIR": str(runtime_home),
            "PATH": str(tmp_path / "empty-bin"),
            "SICO_LEGACY_0114": "1" if wrapper else "0",
            "SICO_API_BASE_URL": "https://gateway.example/v1",
            "SICO_API_KEY": "test-gateway-key",
        }
    )
    for variable in (
        "CAD_HOME", "CAD_CODEX_TERMINAL", "CAD_CODEX_CLI", "NODEJS_HOME",
        "SICO_AI_RELEASE_ROOT", "CAD_TEMP_DIR",
    ):
        environment.pop(variable, None)
    if runtime_source == "configured":
        environment["SICO_AI_RELEASE_ROOT"] = str(release_root)
    agent_args = ["--codex", str(codex_stub)] if runtime_source == "explicit" else []

    process = subprocess.Popen(
        [
            CAD_PYTHON,
            str(release_root / "python/sico-ai"),
            "session",
            "--workspace",
            str(tmp_path),
            *agent_args,
            "--request-timeout",
            "2",
        ],
        cwd=tmp_path,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert _read_event(process) == {
            "event": "session.ready",
            "workspace": str(tmp_path),
            "agent": "codex",
            "control_protocol": "control.v2",
        }
        deadline = 5.0
        while (not log.is_file() or log.stat().st_size == 0) and deadline > 0:
            time.sleep(0.02)
            deadline -= 0.02
        assert log.is_file(), "terminal override was not launched"
        first = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
        assert first["cwd"] == str(tmp_path)
        assert first["workspace"] == str(tmp_path)
        expected_agent = codex_stub if runtime_source == "explicit" else bundled
        assert first["agent"][0] == str(expected_agent.resolve())
        assert (
            f"mcp_servers.sico_ic.cwd={json.dumps(str(release_root / 'python'))}"
            in first["agent"]
        )
        skills_link = tmp_path / "codex-home" / "skills" / "sico-cad-ai"
        assert skills_link.is_symlink()
        assert skills_link.resolve() == (release_root / "skills").resolve()
        assert first["control_fd"] is True
        assert first["has_real_key"] is not wrapper
        assert "test-gateway-key" not in json.dumps(first["agent"])
        if wrapper:
            endpoint = urlsplit(first["model_url"])
            assert endpoint.hostname == "127.0.0.1"
            with socket.create_connection((endpoint.hostname, endpoint.port), timeout=1):
                pass

        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=8) == 0
        if wrapper:
            with pytest.raises(OSError):
                socket.create_connection((endpoint.hostname, endpoint.port), timeout=1)
        records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        assert [record["control"] for record in records[1:]] == ["shutdown"]
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()
