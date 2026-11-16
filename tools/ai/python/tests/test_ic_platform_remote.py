"""Remote discovery retains identity checks and closes the owned SSH tunnel."""

import json
import socket
import time
from concurrent.futures import CancelledError

import pytest
from sico.transport import bridge_client, bridge_tunnel
from sico.transport.bridge_identity import discovery_path
from sico.transport.host_discovery import connect_candidate, list_instances
from test_ic_platform_service import host


class LocalTunnel:
    closed = []

    def __init__(self, node, port, *, deadline, cancelled=None):
        assert node == "remote-node"
        if cancelled and cancelled():
            raise CancelledError()
        self.port = port

    def connect(self, timeout):
        return socket.create_connection(("127.0.0.1", self.port), timeout)

    def close(self):
        self.closed.append(self.port)


def test_remote_descriptor_discovered_and_revalidated(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge_client, "BridgeTunnel", LocalTunnel)
    with host(tmp_path) as (bridge, context):
        descriptor = dict(bridge.descriptor, node="remote-node", job="123")
        bridge.descriptor = descriptor
        bridge.path.unlink()
        remote = discovery_path(tmp_path, context, node="remote-node")
        remote.write_text(json.dumps(descriptor))
        remote.chmod(0o600)
        rows = list_instances(tmp_path)
        assert len(rows) == 1 and rows[0]["node"] == "remote-node" and rows[0]["job"] == "123"
        client, bound = connect_candidate(tmp_path, remote.stem)
        with client:
            assert bound == context
            assert client.platform_environment() is not None
        assert LocalTunnel.closed.count(descriptor["port"]) >= 2
        descriptor["generation"] = "wrong"
        remote.write_text(json.dumps(descriptor))
        assert not list_instances(tmp_path)
        with pytest.raises(ValueError):
            connect_candidate(tmp_path, remote.stem)


def test_tunnel_cancel_reaps_process_and_private_socket(tmp_path, monkeypatch):
    records = []

    class Process:
        def __init__(self, command, environment, cwd):
            self.child = type(
                "Child",
                (),
                dict(
                    poll=lambda _self: None,
                    stdout=type("Stream", (), dict(close=lambda _self: None))(),
                ),
            )()
            self.directory = cwd
            self.closed = False
            assert "BatchMode=yes" in command and "StrictHostKeyChecking=yes" in command
            records.append(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr(bridge_tunnel, "LocalProcess", Process)
    with pytest.raises(CancelledError):
        bridge_tunnel.BridgeTunnel(
            "remote-node", 1234, deadline=time.monotonic() + 1, cancelled=lambda: True
        )
    assert records[0].closed and not records[0].directory.exists()


@pytest.mark.parametrize("node", ["-oProxyCommand=bad", "user@host", "../host", "host\nnext"])
def test_descriptor_host_cannot_inject_ssh_arguments(node):
    with pytest.raises(ValueError):
        bridge_tunnel.node_name(node)
