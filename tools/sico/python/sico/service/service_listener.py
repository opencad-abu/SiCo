"""Bounded nonblocking protocol listener; each socket owns its negotiated state."""

import selectors
import time
from collections import deque
from dataclasses import dataclass, field

from ..transport.framing import FrameDecoder, ProtocolError, encode
from .service_operations import OperationStore
from .service_peer import peer_identity
from .service_protocol import FRAME_LIMIT
from .service_wire import ServiceWire

MAX_CLIENTS = 32
CLIENT_TIMEOUT = 1.0
HEARTBEAT_GRACE = 5.0
MAX_BATCH = 8
MAX_OUTPUT = FRAME_LIMIT * MAX_BATCH
MAX_INPUT = FRAME_LIMIT * MAX_BATCH * 2


@dataclass
class _Peer:
    deadline: float
    decoder: FrameDecoder = field(default_factory=lambda: FrameDecoder(FRAME_LIMIT))
    wire: ServiceWire = None
    output: bytes = b""
    attaching: bool = False
    closing: bool = False
    negotiated: bool = False
    replies: object = field(default_factory=deque)


def serve_ready(listener, descriptor, stopped, lifecycle, allocation, dispatch=None):
    listener.setblocking(False)
    operations = OperationStore()
    with selectors.DefaultSelector() as selector:
        selector.register(listener, selectors.EVENT_READ)
        try:
            while True:
                if stopped.is_set():
                    if dispatch is not None:
                        dispatch.close()
                    lifecycle.signal_shutdown()
                lifecycle.tick(attaching=any(key.data is not None and key.data.attaching
                                             for key in selector.get_map().values()))
                if lifecycle.stopping and listener.fileno() in selector.get_map():
                    selector.unregister(listener)
                if not selector.get_map():
                    break
                for key, _mask in selector.select(0.05):
                    if key.fileobj is listener:
                        if not lifecycle.stopping:
                            _accept(listener, selector, operations, dispatch)
                    else:
                        _exchange(selector, key, descriptor, lifecycle, allocation)
                now = time.monotonic()
                for key in list(selector.get_map().values()):
                    if key.data is not None:
                        try:
                            _flush_replies(selector, key.fileobj, key.data)
                        except (OSError, ProtocolError):
                            _drop(selector, key.fileobj)
                            continue
                    if key.data is not None and lifecycle.stopping:
                        key.data.deadline = min(key.data.deadline, now + CLIENT_TIMEOUT)
                    if key.data is not None and key.data.deadline <= now:
                        _drop(selector, key.fileobj)
        finally:
            for key in list(selector.get_map().values()):
                if key.data is not None:
                    _drop(selector, key.fileobj)


def _accept(listener, selector, operations, dispatch=None):
    try:
        sock, _address = listener.accept()
    except BlockingIOError:
        return
    try:
        peer_identity(sock)
        client_count = sum(key.data is not None for key in selector.get_map().values())
        if client_count >= MAX_CLIENTS:
            sock.close()
            return
        sock.setblocking(False)
        selector.register(sock, selectors.EVENT_READ,
                          _Peer(time.monotonic() + CLIENT_TIMEOUT,
                                wire=ServiceWire(operations, dispatch)))
    except (OSError, ProtocolError):
        sock.close()


def _drop(selector, sock):
    peer = selector.get_key(sock).data
    if peer is not None:
        peer.wire.close()
    selector.unregister(sock)
    sock.close()


def _flush_replies(selector, sock, peer):
    while peer.replies:
        reply = peer.replies[0]
        if not isinstance(reply, dict):
            if not reply.done():
                break
            reply = reply.result()
        encoded = encode(reply, FRAME_LIMIT)
        if len(peer.output) + len(encoded) > MAX_OUTPUT:
            raise ProtocolError("Service output backpressure limit exceeded")
        peer.replies.popleft()
        peer.output += encoded
    selector.modify(sock, selectors.EVENT_WRITE if peer.output else selectors.EVENT_READ, peer)


def _exchange(selector, key, descriptor, lifecycle, allocation):
    sock, peer = key.fileobj, key.data
    try:
        if peer.output:
            count = sock.send(peer.output)
            if count == 0:
                _drop(selector, sock)
                return
            peer.output = peer.output[count:]
            if not peer.output:
                peer.attaching = False
                if peer.closing or lifecycle.stopping:
                    _drop(selector, sock)
                else:
                    selector.modify(sock, selectors.EVENT_READ, peer)
            return
        raw = sock.recv(FRAME_LIMIT + 4)
        if not raw:
            peer.decoder.eof()
            _drop(selector, sock)
            return
        messages = peer.decoder.feed(raw)
        if len(peer.decoder.buffer) > MAX_INPUT:
            raise ProtocolError("Service input backpressure limit exceeded")
        if not messages:
            return
        if len(messages) > MAX_BATCH:
            raise ProtocolError("Service input batch exceeds limit")
        for message in messages:
            if peer.closing:
                raise ProtocolError("Service handshake already rejected")
            if len(peer.replies) >= MAX_BATCH:
                raise ProtocolError("Service pending reply limit exceeded")
            reply = peer.wire.reply(message, descriptor, lifecycle, allocation)
            if isinstance(reply, dict):
                peer.attaching |= reply["kind"] == "ready" and message.get("purpose") == "attach"
                peer.negotiated |= reply["kind"] == "ready"
                peer.closing = reply["kind"] == "rejected"
            peer.replies.append(reply)
        if not lifecycle.stopping:
            peer.deadline = time.monotonic() + (HEARTBEAT_GRACE if peer.negotiated
                                                else CLIENT_TIMEOUT)
        _flush_replies(selector, sock, peer)
    except BlockingIOError:
        return
    except (OSError, ProtocolError):
        _drop(selector, sock)
