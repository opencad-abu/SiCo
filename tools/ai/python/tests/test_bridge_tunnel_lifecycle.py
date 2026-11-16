"""Tunnel failures retain their classification and release only owned resources."""

from concurrent.futures import CancelledError
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from sico.transport import bridge_tunnel


@pytest.fixture
def owned_tunnel(tmp_path, monkeypatch):
    directory = tmp_path / "private-tunnel"
    events = []
    processes = []
    behavior = dict(ready=False, exited=False, spawn_error=False, cleanup_error=False)

    def private_directory(**kwargs):
        directory.mkdir(mode=0o700)
        return str(directory)

    class Process:
        def __init__(self, command, environment, cwd):
            assert cwd == directory
            assert command[:3] == ["ssh", "-N", "-T"]
            assert "BatchMode=yes" in command and "StrictHostKeyChecking=yes" in command
            assert command[-2:] == [f"{directory}/socket:127.0.0.1:1234", "remote-node"]
            if behavior["spawn_error"]:
                raise OSError("supervisor spawn failed")
            self.child = SimpleNamespace(
                poll=lambda: 0 if behavior["exited"] else None,
                stdout=SimpleNamespace(close=lambda: events.append("stdout_closed")),
            )
            if behavior["ready"]:
                (directory / "socket").touch()
            processes.append(self)

        def close(self):
            assert directory.exists()
            events.append("process_closed")
            if behavior["cleanup_error"]:
                raise RuntimeError("Local descendants still require reconciliation")

    monkeypatch.setattr(bridge_tunnel.tempfile, "mkdtemp", private_directory)
    monkeypatch.setattr(bridge_tunnel, "LocalProcess", Process)
    return directory, events, processes, behavior


@pytest.mark.parametrize("failure,exception", [
    ("cancelled", CancelledError), ("timeout", TimeoutError),
    ("exited", OSError), ("spawn_error", OSError),
])
def test_failed_construction_closes_process_before_removing_socket(owned_tunnel, failure, exception):
    directory, events, processes, behavior = owned_tunnel
    behavior[failure] = True
    deadline = time.monotonic() + (-1 if failure == "timeout" else 3)
    with pytest.raises(exception):
        bridge_tunnel.BridgeTunnel("remote-node", 1234, deadline=deadline,
                                   cancelled=lambda: failure == "cancelled")
    assert not directory.exists()
    assert events == ([] if failure == "spawn_error" else ["process_closed", "stdout_closed"])
    assert len(processes) == (0 if failure == "spawn_error" else 1)


def test_ready_tunnel_close_is_idempotent(owned_tunnel):
    directory, events, _, behavior = owned_tunnel
    behavior["ready"] = True
    tunnel = bridge_tunnel.BridgeTunnel("remote-node", 1234, deadline=time.monotonic() + 3)
    assert tunnel.path == directory / "socket" and tunnel.path.exists()
    tunnel.close()
    tunnel.close()
    assert events == ["process_closed", "stdout_closed"]
    assert tunnel.process is None and not directory.exists()


def test_unconfirmed_cleanup_retains_owner_and_directory_for_retry(owned_tunnel):
    directory, events, processes, behavior = owned_tunnel
    behavior.update(ready=True, cleanup_error=True)
    tunnel = bridge_tunnel.BridgeTunnel("remote-node", 1234, deadline=time.monotonic() + 3)
    try:
        with pytest.raises(RuntimeError, match="reconciliation"):
            tunnel.close()
        assert tunnel.process is processes[0] and directory.exists()
        assert events == ["process_closed"]
    finally:
        behavior["cleanup_error"] = False
        tunnel.close()
    assert not directory.exists()


def test_connect_failure_closes_only_the_failed_connection(monkeypatch, tmp_path):
    events = []
    tunnel = object.__new__(bridge_tunnel.BridgeTunnel)
    tunnel.path = Path(tmp_path) / "socket"

    def refused(path):
        assert path == str(tunnel.path)
        raise OSError("socket refused")

    connection = SimpleNamespace(
        settimeout=lambda value: events.append(value), connect=refused,
        close=lambda: events.append("connection_closed"),
    )
    monkeypatch.setattr(bridge_tunnel.socket, "socket", lambda family: connection)
    with pytest.raises(OSError, match="socket refused"):
        tunnel.connect(.5)
    assert events == [.5, "connection_closed"]
