"""Bounded sends classify written bytes and retire ambiguous connections."""

import socket
import threading
import time
from concurrent.futures import CancelledError

import pytest
from agent_worker_fixtures import heartbeat
from PyQt5.QtWidgets import QLabel

from sico.service.service_channel import ServiceChannel
from sico.service.service_errors import RequestUnsent, ResultUnknown
from sico.service.service_handshake import ConnectionIdentity
from sico.service.service_messages import ServiceRequest
from sico.service.service_protocol import CAPABILITIES, heartbeat_ack, result_message
from sico.transport.framing import Connection, ProtocolError, SendError, SendTimeout


def identity():
    return ConnectionIdentity("a" * 32, "b" * 32, "c" * 32, "d" * 32, CAPABILITIES)


class Peer:
    def __init__(self, *, send_error=None, receive_error=None):
        self.send_error, self.receive_error = send_error, receive_error
        self.closed, self.requests = False, []

    def send(self, row, **_kwargs):
        self.requests.append(row)
        if self.send_error:
            raise self.send_error

    def receive(self, **_kwargs):
        if self.receive_error:
            raise self.receive_error
        return result_message(self.requests[-1], {})

    def close(self):
        self.closed = True


@pytest.mark.parametrize("error,expected", [
    (SendTimeout(0), RequestUnsent), (SendTimeout(3), ResultUnknown),
    (SendError(0, BrokenPipeError()), RequestUnsent),
    (SendError(3, BrokenPipeError()), ResultUnknown),
])
def test_send_failure_retains_operation_and_prevents_frame_reuse(error, expected):
    peer = Peer(send_error=error)
    channel, request = ServiceChannel(peer, identity()), ServiceRequest("service.stop")
    with pytest.raises(expected) as raised:
        channel.exchange(request, deadline=time.monotonic() + 1)
    if expected is ResultUnknown:
        assert raised.value.operation_id == request.operation_id
        assert raised.value.method == request.method
    assert peer.closed and len(peer.requests) == 1
    with pytest.raises(ProtocolError, match="unusable"):
        channel.exchange(request, deadline=time.monotonic() + 1)
    assert len(peer.requests) == 1


@pytest.mark.parametrize("error", [TimeoutError(), EOFError(), ConnectionResetError()])
def test_receive_loss_is_unknown_and_retires_connection(error):
    peer = Peer(receive_error=error)
    channel, request = ServiceChannel(peer, identity()), ServiceRequest()
    with pytest.raises(ResultUnknown) as raised:
        channel.exchange(request, deadline=time.monotonic() + 1)
    assert raised.value.operation_id == request.operation_id and peer.closed
    with pytest.raises(ProtocolError, match="unusable"):
        channel.exchange(request, deadline=time.monotonic() + 1)


def test_expired_send_does_not_touch_peer_or_poison_fresh_connection():
    peer = Peer()
    channel = ServiceChannel(peer, identity())
    with pytest.raises(RequestUnsent):
        channel.exchange(ServiceRequest(), deadline=time.monotonic() - 1)
    assert not peer.closed and not peer.requests


@pytest.mark.parametrize("prefill", [False, True])
def test_real_socket_backpressure_times_out_with_correct_progress(prefill):
    writer, reader = socket.socketpair()
    with writer, reader:
        writer.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        if prefill:
            writer.setblocking(False)
            while True:
                try:
                    writer.send(b"x" * 4096)
                except BlockingIOError:
                    break
        writer.settimeout(0.5)
        with pytest.raises(SendTimeout) as raised:
            Connection(writer).send({"data": "x" * 200000}, deadline=time.monotonic() + 0.1)
        assert (raised.value.sent == 0) == prefill
        assert writer.gettimeout() == 0.5


@pytest.mark.parametrize("cancel", [False, True])
def test_waiting_for_writer_has_its_own_deadline_and_keeps_qt_live(cancel):
    entered, release = threading.Event(), threading.Event()
    failures = []

    class PausedSocket:
        def sendall(self, _frame):
            entered.set()
            assert release.wait(3)

    connection = Connection(PausedSocket())

    def first_send():
        try:
            connection.send({"first": True})
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=first_send)
    thread.start()
    widget = QLabel("waiting")
    try:
        assert entered.wait(1)
        with pytest.raises(CancelledError if cancel else SendTimeout):
            connection.send({"second": True}, deadline=time.monotonic() + 0.05,
                            cancelled=lambda: cancel)
        heartbeat(widget)
        assert not release.is_set() and thread.is_alive()
    finally:
        release.set()
        thread.join(2)
        widget.close()
    assert not thread.is_alive() and not failures


def test_zero_byte_socket_send_is_disconnect(monkeypatch):
    class ClosedSocket:
        def gettimeout(self):
            return None

        def setblocking(self, _flag):
            pass

        def settimeout(self, _timeout):
            pass

        def send(self, _frame):
            return 0

    monkeypatch.setattr("sico.transport.framing.select.select",
                        lambda *_: ([], [1], []))
    with pytest.raises(SendError) as raised:
        Connection(ClosedSocket()).send({"test": True}, deadline=time.monotonic() + 1)
    assert raised.value.sent == 0 and isinstance(raised.value.__cause__, BrokenPipeError)


@pytest.mark.parametrize("sent", [0, 3])
def test_connection_reports_progress_on_socket_oserror(monkeypatch, sent):
    class Socket:
        def __init__(self):
            self.calls = 0

        def gettimeout(self):
            return None

        def setblocking(self, _flag):
            pass

        def settimeout(self, _timeout):
            pass

        def send(self, _frame):
            self.calls += 1
            if sent and self.calls == 1:
                return sent
            raise BrokenPipeError()

    monkeypatch.setattr("sico.transport.framing.select.select",
                        lambda *_: ([], [1], []))
    with pytest.raises(SendError) as raised:
        Connection(Socket()).send({"test": True}, deadline=time.monotonic() + 1)
    assert raised.value.sent == sent and isinstance(raised.value.__cause__, BrokenPipeError)


def test_heartbeat_timeout_retires_channel():
    peer = Peer(receive_error=TimeoutError())
    channel = ServiceChannel(peer, identity())
    with pytest.raises(TimeoutError):
        channel.heartbeat(deadline=time.monotonic() + 1)
    assert peer.closed
    with pytest.raises(ProtocolError, match="unusable"):
        channel.heartbeat(deadline=time.monotonic() + 1)


@pytest.mark.parametrize("field,value", [
    ("connection_id", "e" * 32), ("service_id", "e" * 32),
    ("project_id", "e" * 32), ("client_id", "e" * 32),
    ("kind", "ready"), ("extra", True),
])
def test_corrupt_heartbeat_ack_retires_channel_without_sending_business_request(field, value):
    class CorruptPeer(Peer):
        def receive(self, **_kwargs):
            return dict(heartbeat_ack(identity()), **{field: value})

    peer = CorruptPeer()
    channel = ServiceChannel(peer, identity())
    with pytest.raises(ProtocolError):
        channel.heartbeat(deadline=time.monotonic() + 1)
    assert peer.closed
    with pytest.raises(ProtocolError, match="unusable"):
        channel.exchange(ServiceRequest("service.stop"), deadline=time.monotonic() + 1)
    assert [row["kind"] for row in peer.requests] == ["heartbeat"]
