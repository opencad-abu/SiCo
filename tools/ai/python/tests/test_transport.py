from __future__ import annotations

import io
import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from cadai.mcp import TOOLS
from cadai.transport import VirtuosoTransport

AI_ROOT = Path(__file__).resolve().parents[2]
CAD_PYTHON = os.environ.get("CAD_PYTHON") or sys.executable


def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition did not become true before the deadline")
        time.sleep(0.02)


def _read_controller_event(process: subprocess.Popen[bytes], timeout: float = 5.0) -> dict:
    assert process.stdout is not None
    ready, _writable, _exceptional = select.select([process.stdout], [], [], timeout)
    if not ready:
        raise AssertionError("controller did not emit an event before the deadline")
    raw = process.stdout.readline()
    if not raw:
        raise AssertionError("controller stdout closed before an event was emitted")
    return json.loads(raw)


def _fake_control_terminal(tmp_path: Path) -> tuple[Path, Path]:
    log = tmp_path / "terminal-control.log"
    terminal = tmp_path / "fake-control-terminal"
    terminal.write_text(
        "\n".join(
            (
                f"#!{CAD_PYTHON}",
                "import os",
                "from pathlib import Path",
                "control_fd = int(os.environ.pop('SICO_AI_CONTROL_FD'))",
                "log = Path(os.environ['FAKE_TERMINAL_CONTROL_LOG'])",
                "with log.open('w', encoding='ascii') as output:",
                "    output.write(f'{os.getpid()}\\n')",
                "    output.flush()",
                "    with os.fdopen(control_fd, 'rb', buffering=0) as control:",
                "        for raw in control:",
                "            command = raw.decode('ascii').strip()",
                "            output.write(command + '\\n')",
                "            output.flush()",
                "            if command == 'shutdown':",
                "                break",
                "    output.write('EOF\\n')",
                "    output.flush()",
                "",
            )
        ),
        encoding="utf-8",
    )
    terminal.chmod(0o755)
    return terminal, log


def _controller_environment(tmp_path: Path, log: Path) -> dict[str, str]:
    runtime_base = tmp_path / "runtime"
    runtime_base.mkdir(mode=0o700)
    environment = os.environ.copy()
    environment.update(
        {
            "CAD_CODEX_HOME": str(tmp_path / "codex-home"),
            "FAKE_TERMINAL_CONTROL_LOG": str(log),
            "PYTHONDONTWRITEBYTECODE": "1",
            "XDG_RUNTIME_DIR": str(runtime_base),
            "SICO_TEMP_DIR": str(tmp_path / ".sico"),
        }
    )
    return environment


def _start_controlled_session(
    tmp_path: Path, codex_stub: Path
) -> tuple[subprocess.Popen[bytes], Path, Path]:
    terminal, log = _fake_control_terminal(tmp_path)
    environment = _controller_environment(tmp_path, log)
    process = subprocess.Popen(
        [
            CAD_PYTHON,
            str(AI_ROOT / "python/sico-ai"),
            "session",
            "--workspace",
            str(tmp_path),
            "--terminal",
            str(terminal),
            "--codex",
            str(codex_stub),
            "--request-timeout",
            "2",
        ],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert _read_controller_event(process) == {
        "event": "session.ready",
        "workspace": str(tmp_path),
        "agent": "codex",
        "control_protocol": "control.v2",
    }
    _wait_for(log.is_file)
    return process, log, Path(environment["XDG_RUNTIME_DIR"])


def test_close_joins_reader_while_stdin_pipe_remains_open() -> None:
    read_fd, write_fd = os.pipe()
    reader = os.fdopen(read_fd, "rb", buffering=0)
    transport = VirtuosoTransport(reader=reader, writer=io.BytesIO())
    try:
        transport.start()
        thread = transport._thread
        assert thread is not None and thread.is_alive()

        transport.close()

        assert not thread.is_alive()
    finally:
        os.close(write_fd)
        reader.close()


def test_transport_routes_terminal_show_event_and_reports_eof() -> None:
    read_fd, write_fd = os.pipe()
    reader = os.fdopen(read_fd, "rb", buffering=0)
    transport = VirtuosoTransport(reader=reader, writer=io.BytesIO())
    try:
        transport.start()
        os.write(write_fd, b'{"event":"terminal.show"}\n')

        assert transport.next_event(timeout=1) == "terminal.show"
        assert transport.next_event(timeout=0) is None

        os.close(write_fd)
        write_fd = -1
        assert transport.wait_closed(timeout=1)
        assert transport.is_closed
    finally:
        transport.close()
        if write_fd >= 0:
            os.close(write_fd)
        reader.close()


def test_transport_ignores_unknown_or_extended_control_events() -> None:
    transport = VirtuosoTransport(reader=io.BytesIO(), writer=io.BytesIO())
    assert transport.route_event({"event": "terminal.hide"}) is False
    assert transport.route_event({"event": "terminal.show", "unexpected": True}) is False
    assert transport.next_event(timeout=0) is None


def test_transport_coalesces_repeated_terminal_show_events() -> None:
    transport = VirtuosoTransport(reader=io.BytesIO(), writer=io.BytesIO())
    assert transport.route_event({"event": "terminal.show"}) is True
    assert transport.route_event({"event": "terminal.show"}) is True
    assert transport.next_event(timeout=0) == "terminal.show"
    assert transport.next_event(timeout=0) is None


def test_virtuoso_eof_sends_show_then_shutdown_without_hanging(
    tmp_path: Path, codex_stub: Path
) -> None:
    process, log, runtime_base = _start_controlled_session(tmp_path, codex_stub)
    assert process.stdin is not None
    try:
        process.stdin.write(b'{"event":"terminal.show"}\n')
        process.stdin.flush()
        _wait_for(lambda: "show\n" in log.read_text(encoding="ascii"))

        process.stdin.close()
        return_code = process.wait(timeout=8)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()

    assert process.stderr is not None
    stderr = process.stderr.read()
    process.stderr.close()
    assert return_code == 0, stderr.decode(errors="replace")
    assert log.read_text(encoding="ascii").splitlines()[1:] == ["show", "shutdown", "EOF"]
    assert not list(runtime_base.glob("cad-ai-*"))


def test_sigterm_requests_cooperative_terminal_shutdown(tmp_path: Path, codex_stub: Path) -> None:
    process, log, runtime_base = _start_controlled_session(tmp_path, codex_stub)
    assert process.stdin is not None
    try:
        process.send_signal(signal.SIGTERM)
        return_code = process.wait(timeout=8)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdin.close()

    assert process.stderr is not None
    stderr = process.stderr.read()
    process.stderr.close()
    assert return_code == 0, stderr
    assert log.read_text(encoding="ascii").splitlines()[1:] == ["shutdown", "EOF"]
    assert not list(runtime_base.glob("cad-ai-*"))


def test_controller_sigkill_closes_terminal_control_pipe(tmp_path: Path, codex_stub: Path) -> None:
    process, log, _runtime_base = _start_controlled_session(tmp_path, codex_stub)
    terminal_pid = int(log.read_text(encoding="ascii").splitlines()[0])
    try:
        process.kill()
        assert process.wait(timeout=5) == -signal.SIGKILL
        _wait_for(lambda: log.read_text(encoding="ascii").splitlines()[-1:] == ["EOF"])
        _wait_for(lambda: not Path(f"/proc/{terminal_pid}").exists())
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()

    assert log.read_text(encoding="ascii").splitlines()[1:] == ["EOF"]


@pytest.mark.parametrize(
    ("agent", "executable_option"),
    (("codex", "--codex"), ("claude", "--claude")),
)
def test_session_exits_cleanly_while_virtuoso_stdin_remains_open(
    tmp_path: Path, agent: str, executable_option: str, codex_stub: Path
) -> None:
    runtime_base = tmp_path / "runtime"
    runtime_base.mkdir(mode=0o700)
    env = os.environ.copy()
    env.update(
        {
            "CAD_CODEX_HOME": str(tmp_path / "codex-home"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "XDG_RUNTIME_DIR": str(runtime_base),
            "SICO_TEMP_DIR": str(tmp_path / ".sico"),
        }
    )
    process = subprocess.Popen(
        [
            CAD_PYTHON,
            str(AI_ROOT / "python/sico-ai"),
            "session",
            "--agent",
            agent,
            "--workspace",
            str(tmp_path),
            "--terminal",
            "/bin/true",
            executable_option,
            str(codex_stub) if agent == "codex" else "/bin/true",
        ],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        return_code = process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    process.stdin.close()
    stdout = process.stdout.read()
    stderr = process.stderr.read()
    process.stdout.close()
    process.stderr.close()

    assert return_code == 0, stdout + stderr
    assert b"Fatal Python error" not in stderr
    assert not list(runtime_base.glob("cad-ai-*"))


def test_claude_session_uses_private_mcp_config_for_live_tool_call(tmp_path: Path) -> None:
    runtime_base = tmp_path / "runtime"
    runtime_base.mkdir(mode=0o700)
    marker = tmp_path / "claude-mcp.ok"
    fake_terminal = tmp_path / "fake-terminal"
    fake_terminal.write_text(
        "\n".join(
            (
                f"#!{CAD_PYTHON}",
                "import os",
                "import sys",
                "separator = sys.argv.index('--')",
                "command = sys.argv[separator + 1:]",
                "os.execv(command[0], command)",
                "",
            )
        ),
        encoding="utf-8",
    )
    fake_terminal.chmod(0o755)
    expected_tool_names = {tool["name"] for tool in TOOLS}
    fake_claude = tmp_path / "fake-claude"
    fake_claude.write_text(
        "\n".join(
            (
                f"#!{CAD_PYTHON}",
                "import json",
                "import os",
                "import subprocess",
                "import sys",
                "from pathlib import Path",
                "config_path = Path(sys.argv[sys.argv.index('--mcp-config') + 1])",
                "server = json.loads(config_path.read_text(encoding='utf-8'))",
                "server = server['mcpServers']['sico_ic']",
                "environment = os.environ.copy()",
                "environment.update(server['env'])",
                "process = subprocess.Popen(",
                "    [server['command'], *server['args']],",
                "    env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)",
                "def exchange(message):",
                "    process.stdin.write(json.dumps(message) + '\\n')",
                "    process.stdin.flush()",
                "    return json.loads(process.stdout.readline())",
                "initialized = exchange({'jsonrpc': '2.0', 'id': 1,",
                "    'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}})",
                "process.stdin.write(json.dumps({'jsonrpc': '2.0',",
                "    'method': 'notifications/initialized'}) + '\\n')",
                "process.stdin.flush()",
                "listed = exchange({'jsonrpc': '2.0', 'id': 2,",
                "    'method': 'tools/list', 'params': {}})",
                "called = exchange({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',",
                "    'params': {'name': 'get_context', 'arguments': {}}})",
                "payload = json.loads(called['result']['content'][0]['text'])",
                "names = [tool['name'] for tool in listed['result']['tools']]",
                f"expected = {expected_tool_names!r}",
                "ok = (initialized['result']['serverInfo']['name'] == 'sico_ic'",
                "    and set(names) == expected and len(names) == len(expected)",
                "    and called['result']['isError'] is False",
                "    and payload['cwd'] == '/fake-virtuoso'",
                "    and 'CAD_CODEX_API_KEY' not in os.environ)",
                "if ok:",
                "    Path(os.environ['FAKE_CLAUDE_MARKER']).write_text('CLAUDE_MCP_OK\\n')",
                "process.stdin.close()",
                "mcp_status = process.wait()",
                "raise SystemExit(mcp_status if ok else 3)",
                "",
            )
        ),
        encoding="utf-8",
    )
    fake_claude.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "CAD_CODEX_API_KEY": "must-not-reach-claude",
            "FAKE_CLAUDE_MARKER": str(marker),
            "PYTHONDONTWRITEBYTECODE": "1",
            "XDG_RUNTIME_DIR": str(runtime_base),
            "SICO_TEMP_DIR": str(tmp_path / ".sico"),
        }
    )
    process = subprocess.Popen(
        [
            CAD_PYTHON,
            str(AI_ROOT / "python/sico-ai"),
            "session",
            "--agent",
            "claude",
            "--workspace",
            str(tmp_path),
            "--terminal",
            str(fake_terminal),
            "--claude",
            str(fake_claude),
            "--request-timeout",
            "5",
        ],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    messages = []

    def serve_virtuoso() -> None:
        for raw in process.stdout:
            message = json.loads(raw)
            messages.append(message)
            if message.get("method") not in {"eval_skill_native", "get_context"}:
                continue
            Path(message["output_path"]).write_text(
                json.dumps({"ok": True, "cwd": "/fake-virtuoso"}),
                encoding="utf-8",
            )
            response = {
                "id": message["id"],
                "ok": True,
                "result": {"result_path": message["output_path"]},
            }
            process.stdin.write((json.dumps(response) + "\n").encode())
            process.stdin.flush()

    bridge = threading.Thread(target=serve_virtuoso, daemon=True)
    bridge.start()
    try:
        return_code = process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdin.close()
    bridge.join(timeout=2)
    assert not bridge.is_alive()
    assert process.stderr is not None
    stderr = process.stderr.read()
    process.stdout.close()
    process.stderr.close()

    assert return_code == 0, stderr.decode(errors="replace")
    assert marker.read_text(encoding="utf-8") == "CLAUDE_MCP_OK\n"
    assert [message.get("event") for message in messages if "event" in message] == [
        "session.ready",
        "session.exit",
    ]
    assert [message.get("method") for message in messages if "method" in message] == [
        "eval_skill_native", "get_context",
    ]
    assert not list(runtime_base.glob("cad-ai-*"))
