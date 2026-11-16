"""Transport failure contracts after moving SocketClient out of the server."""

import io
from types import SimpleNamespace

import pytest

from cadai import mcp_transport
from cadai.mcp import SocketClient as CompatSocketClient
from cadai.mcp_transport import SocketClient
from cadai.protocol import encode_line
from cadai.runtime import RuntimePaths


@pytest.mark.parametrize("response", [
    b"",
    b'{"id":"truncated"}',
    encode_line({"id": "wrong-id", "ok": True, "result": {}}),
    TimeoutError("fixture timeout"),
])
def test_response_failure_closes_bridge_and_removes_spool_without_retry(tmp_path, response):
    spool = tmp_path / "spool"
    spool.mkdir(mode=0o700)
    runtime = RuntimePaths(tmp_path, tmp_path / "bridge.sock", spool)
    client = SocketClient(runtime.socket, "secret", 2.0, runtime)
    sent, closed = [], []

    class Reader(io.BytesIO):
        def readline(self, size=-1):
            if isinstance(response, Exception):
                raise response
            return response

    reader = Reader()
    client._socket = SimpleNamespace(
        sendall=sent.append, close=lambda: closed.append("socket"),
    )
    client._reader = reader
    ok, detail = client.call("eval_skill", {"code": "x" * 32_769})
    assert not ok and detail["code"] == "bridge_unavailable"
    assert "not retried" in detail["message"]
    assert "after request submission; execution status is unknown" in detail["message"]
    assert "Verify the operation state" in detail["message"]
    assert len(sent) == 1 and closed == ["socket"] and reader.closed
    assert not list(spool.iterdir())


def test_bounded_call_restores_timeout_and_keeps_compat_class_identity(tmp_path, monkeypatch):
    runtime = RuntimePaths(tmp_path, tmp_path / "bridge.sock", tmp_path / "spool")
    client = SocketClient(runtime.socket, "secret", 20.0, runtime)
    timeouts = []
    client._socket = SimpleNamespace(sendall=lambda data: None, settimeout=timeouts.append)
    client._reader = io.BytesIO(encode_line({"id": "a" * 32, "ok": True, "result": {}}))
    monkeypatch.setattr(mcp_transport.uuid, "uuid4", lambda: SimpleNamespace(hex="a" * 32))
    assert client.call_bounded("get_context", {}, 0.5) == (True, {})
    assert timeouts == [0.5, 20.0] and client.timeout == 20.0
    assert CompatSocketClient is SocketClient
