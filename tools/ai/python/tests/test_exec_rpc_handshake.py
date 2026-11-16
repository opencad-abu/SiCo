"""The existing RPC framing supports both pinned Codex initialization contracts."""

import os
import sys
import threading
from types import SimpleNamespace

import pytest

from sico.codex.rpc import AppServer
from sico.local_process import LocalProcess


@pytest.mark.parametrize("protocol", ["app-server", "exec-server"])
def test_real_rpc_handshake_preserves_default_and_selects_explicit_exec_server(tmp_path, protocol):
    script = tmp_path / "server.py"
    script.write_text('''import json,sys
for line in sys.stdin:
    frame=json.loads(line)
    if frame.get('method') == 'initialize':
        print(json.dumps(dict(id=frame['id'],result=frame['params'])),flush=True)
''')
    server = AppServer([sys.executable, "-s", str(script)], {}, tmp_path, protocol=protocol)
    try:
        if protocol == "exec-server":
            assert server.initialization == {"clientName": "sico-terminal"}
        else:
            assert server.initialization["capabilities"] == {"experimentalApi": True}
            assert server.initialization["clientInfo"]["name"] == "cad_copilot"
    finally:
        server.close(require_confirmed=True)


def test_invalid_handshake_is_rejected_before_process_creation(monkeypatch, tmp_path):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Invalid protocol created a process")
    monkeypatch.setattr("sico.codex.rpc.LocalProcess", forbidden)
    with pytest.raises(ValueError):
        AppServer(["unused"], {}, tmp_path, protocol="unknown")


@pytest.mark.parametrize("status", [0, 137, -9])
def test_terminal_cleanup_never_accepts_a_crashed_supervisor_as_confirmed(status):
    owner = object.__new__(LocalProcess)
    owner._lock = threading.Lock()
    read, owner._control = os.pipe()
    owner.child = SimpleNamespace(poll=lambda: status, returncode=status,
                                  stdin=SimpleNamespace(close=lambda: None), wait=lambda timeout: None)
    try:
        if status == 0:
            owner.close(require_confirmed=True)
        else:
            with pytest.raises(RuntimeError, match="not confirmed"):
                owner.close(require_confirmed=True)
        assert os.read(read, 1) == b""
    finally:
        os.close(read)


@pytest.mark.parametrize('previous_close', [False, True])
def test_child_exit_status_does_not_prove_supervisor_cleanup(previous_close):
    owner = object.__new__(LocalProcess)
    owner._lock = threading.Lock()
    owner._control = -1
    owner._status = None
    owner._exit_status = 0  # Command status can be published before reaping.
    owner._closed = previous_close
    owner.child = SimpleNamespace(poll=lambda: -9, returncode=-9,
        stdin=SimpleNamespace(close=lambda: None), wait=lambda timeout: None)
    for _ in range(2):
        with pytest.raises(RuntimeError, match='not confirmed'):
            owner.close(require_confirmed=True)


def test_rpc_repeated_close_cannot_erase_failed_cleanup():
    server = object.__new__(AppServer)
    failure = RuntimeError('cleanup still unconfirmed')
    server._closed = True
    server._cleanup_error = failure
    with pytest.raises(RuntimeError, match='still unconfirmed'):
        server.close(require_confirmed=True)
