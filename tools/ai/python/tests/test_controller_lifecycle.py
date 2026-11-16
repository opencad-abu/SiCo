from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

import pytest

import cadai.controller as controller_module
import cadai.terminal_control as terminal_control_module
from cadai.runtime import RuntimePaths


@pytest.fixture(autouse=True)
def isolated_launch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CAD_TEMP_DIR", raising=False)
    monkeypatch.delenv("SICO_TEMP_DIR", raising=False)


@pytest.fixture
def runtime(tmp_path: Path) -> RuntimePaths:
    root = tmp_path / "node-local" / "cad-ai-test"
    spool = root / "spool"
    bridge = (
        tmp_path
        / "shared"
        / ".cad"
        / "ai"
        / f".cad-ai-runtime-{os.getuid()}"
        / "session-test"
    )
    spool.mkdir(parents=True, mode=0o700)
    bridge.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    spool.chmod(0o700)
    bridge.parent.chmod(0o700)
    bridge.chmod(0o700)
    return RuntimePaths(root, root / "bridge.sock", spool, bridge_spool=bridge)


def test_startup_failure_cleans_private_runtime(
    runtime: RuntimePaths, tmp_path: Path, monkeypatch, codex_stub: Path
) -> None:
    class StartupTransport:
        def start(self) -> None:
            pass

        def close(self) -> None:
            pass

    class StartupServer:
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

    monkeypatch.setattr(
        controller_module.RuntimePaths, "create", lambda **_kwargs: runtime
    )
    monkeypatch.setattr(controller_module, "VirtuosoTransport", StartupTransport)
    monkeypatch.setattr(controller_module, "UnixRequestServer", StartupServer)
    monkeypatch.delenv("CAD_HOME", raising=False)

    def fail_home() -> Path:
        raise RuntimeError("cannot create Codex home")

    monkeypatch.setattr(controller_module, "private_codex_home", fail_home)
    with pytest.raises(RuntimeError, match="cannot create"):
        controller_module.run_session(tmp_path, "/bin/true", str(codex_stub), 300)
    assert not runtime.root.exists()


def test_sigterm_handler_is_installed_before_runtime_creation(
    tmp_path: Path, monkeypatch, codex_stub: Path
) -> None:
    events: list[str] = []

    def track_signal(_signum, _handler):
        events.append("signal")
        return signal.SIG_DFL

    def fail_runtime_creation(**_kwargs):
        events.append("runtime")
        raise RuntimeError("runtime creation failed")

    monkeypatch.setattr(controller_module.signal, "signal", track_signal)
    monkeypatch.setattr(controller_module.RuntimePaths, "create", fail_runtime_creation)
    monkeypatch.delenv("CAD_HOME", raising=False)

    with pytest.raises(RuntimeError, match="runtime creation failed"):
        controller_module.run_session(tmp_path, "/bin/true", str(codex_stub), 300)

    assert events == ["signal", "runtime", "signal"]


def test_terminal_shutdown_escalates_without_an_unbounded_wait() -> None:
    class StuckProcess:
        def __init__(self) -> None:
            self.terminated = False
            self.killed = False
            self.wait_timeouts: list[float] = []

        def poll(self):
            return -9 if self.killed else None

        def wait(self, timeout=None):
            self.wait_timeouts.append(timeout)
            if self.killed:
                return -9
            raise subprocess.TimeoutExpired("terminal", timeout)

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

    process = StuckProcess()
    control = terminal_control_module.TerminalControl.create()
    try:
        assert terminal_control_module.shutdown_terminal(process, control) == -9
    finally:
        control.close()

    assert process.terminated is True
    assert process.killed is True
    assert process.wait_timeouts == [
        terminal_control_module.TERMINAL_SHUTDOWN_TIMEOUT,
        terminal_control_module.TERMINAL_TERMINATE_TIMEOUT,
        terminal_control_module.TERMINAL_TERMINATE_TIMEOUT,
    ]


@pytest.mark.parametrize("wrapper", [False, True])
def test_terminal_start_failure_closes_both_control_descriptors(
    runtime: RuntimePaths, tmp_path: Path, monkeypatch, capsys, wrapper: bool
) -> None:
    class StartupTransport:
        def start(self) -> None:
            pass

        def close(self, _reason: str = "") -> None:
            pass

    class StartupServer:
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

    read_fd, write_fd = os.pipe()
    monkeypatch.setattr(
        controller_module.RuntimePaths, "create", lambda **_kwargs: runtime
    )
    monkeypatch.setattr(controller_module, "VirtuosoTransport", StartupTransport)
    monkeypatch.setattr(controller_module, "UnixRequestServer", StartupServer)
    monkeypatch.setattr(
        controller_module, "production_python", lambda **_kwargs: "/bin/true"
    )
    monkeypatch.setattr(terminal_control_module.os, "pipe", lambda: (read_fd, write_fd))
    terminal_environment = {
        "SICO_LEGACY_0114": "1" if wrapper else "0",
        "SICO_API_BASE_URL": "https://gateway.example/v1",
        "SICO_API_KEY": "test-secret",
    }
    prepared = []
    prepare = controller_module.prepare_codex_gateway

    def track_wrapper(environment):
        proxy = prepare(environment)
        if proxy:
            prepared.append(proxy)
        return proxy

    monkeypatch.setattr(controller_module, "prepare_codex_gateway", track_wrapper)
    monkeypatch.setattr(
        controller_module,
        "prepare_terminal_environment",
        lambda *_args, **_kwargs: (terminal_environment, ("input preflight needs attention",)),
    )
    monkeypatch.setenv("SICO_CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.delenv("CAD_HOME", raising=False)
    monkeypatch.setattr(controller_module, "resolve_agent_executable", lambda *a, **kw: "/bin/true")

    def fail_start(*_args, **kwargs):
        assert kwargs["pass_fds"] == (read_fd,)
        assert kwargs["env"]["SICO_AI_CONTROL_FD"] == str(read_fd)
        raise OSError("terminal did not start")

    monkeypatch.setattr(controller_module.subprocess, "Popen", fail_start)
    with pytest.raises(OSError, match="terminal did not start"):
        controller_module.run_session(tmp_path, "/bin/true", "/bin/true", 300)

    for descriptor in (read_fd, write_fd):
        with pytest.raises(OSError):
            os.fstat(descriptor)
    assert not runtime.root.exists()
    assert len(prepared) == int(wrapper)
    assert all(proxy.stopped.is_set() and not proxy.thread.is_alive() for proxy in prepared)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "sico-ai: warning: input preflight needs attention\n"
