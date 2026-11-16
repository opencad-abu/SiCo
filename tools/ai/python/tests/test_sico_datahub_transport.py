"""Transport adapters move validated envelopes without interpreting payloads."""

import io
import socket
import sys
import time

import pytest
from sico_datahub.envelope import Envelope
from sico_datahub.errors import HubError
from sico_datahub.transport import (
    InProcessTransport,
    StdioTransport,
    SubprocessTransport,
    TransportClosed,
    UnixTransport,
)


def _envelope(request_id="o1", payload=None):
    return Envelope(
        contract="sico.cad.objects.v1",
        action="list",
        id=request_id,
        payload={"window_ref": "wctx_fixture_1"} if payload is None else payload,
    )


def test_in_process_pair_preserves_identity_and_payload():
    left, right = InProcessTransport.pair("left", "right")
    sent = _envelope(payload={"opaque": "domain-owned", "items": []})
    left.send(sent)
    assert right.receive(time.monotonic() + 1) == sent
    assert left.identity() == "left" and right.identity() == "right"
    left.close()
    with pytest.raises(TransportClosed):
        right.receive(time.monotonic() + 1)
    with pytest.raises(TransportClosed):
        left.send(sent)


def test_in_process_receive_deadline_is_bounded():
    left, right = InProcessTransport.pair()
    with pytest.raises(HubError) as failure:
        right.receive(time.monotonic() + 0.01)
    assert failure.value.code == "timeout"
    left.close()
    right.close()


def test_stdio_roundtrip_and_invalid_line_rejection():
    output = io.BytesIO()
    sender = StdioTransport(io.BytesIO(), output, identity="sender")
    envelope = _envelope()
    sender.send(envelope)
    receiver = StdioTransport(io.BytesIO(output.getvalue()), io.BytesIO())
    assert receiver.receive(time.monotonic() + 1) == envelope

    with pytest.raises(HubError, match="Invalid or oversized"):
        StdioTransport(io.BytesIO(b"{}"), io.BytesIO()).receive()
    with pytest.raises(HubError):
        StdioTransport(
            io.BytesIO(b'{"payload":{}}\n'), io.BytesIO()
        ).receive()


def test_stdio_enforces_frame_limit_before_write():
    sender = StdioTransport(io.BytesIO(), io.BytesIO(), max_bytes=80)
    with pytest.raises(HubError) as failure:
        sender.send(_envelope(payload={"value": "x" * 200}))
    assert failure.value.code == "result_too_large"


def test_unix_socketpair_reuses_length_prefixed_framing():
    first_socket, second_socket = socket.socketpair()
    first = UnixTransport(first_socket, identity="first")
    second = UnixTransport(second_socket, identity="second")
    try:
        envelope = _envelope("socket-1")
        first.send(envelope)
        assert second.receive(time.monotonic() + 2) == envelope
        second.send(_envelope("socket-2"))
        assert first.receive(time.monotonic() + 2).id == "socket-2"
    finally:
        first.close()
        second.close()


def test_subprocess_stdio_roundtrip_and_shutdown():
    command = [
        sys.executable,
        "-I",
        "-c",
        "import sys\n"
        "for line in sys.stdin.buffer:\n"
        "    sys.stdout.buffer.write(line)\n"
        "    sys.stdout.buffer.flush()\n",
    ]
    with SubprocessTransport(command, identity="echo") as transport:
        envelope = _envelope("subprocess-1")
        transport.send(envelope)
        assert transport.receive(time.monotonic() + 2) == envelope
        assert transport.identity() == "echo"
    assert transport._process.poll() is not None
