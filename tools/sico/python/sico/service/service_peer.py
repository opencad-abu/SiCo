"""Authenticate the owned local Unix endpoint and its OS process identity."""

import os
import socket
import struct
import time
from concurrent.futures import CancelledError
from contextlib import contextmanager

from ..transport.framing import Connection, ProtocolError
from .background_watchdog import process_identity
from .service_discovery import local_host
from .service_protocol import FRAME_LIMIT
from .service_runtime import validate_endpoint


def peer_identity(sock, *, expected_pid=None):
    pid, uid, _gid = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if uid != os.getuid() or (expected_pid is not None and pid != expected_pid):
        raise ProtocolError("Service peer identity mismatch")
    return pid


def check_wait(deadline, cancelled):
    if cancelled is not None and cancelled():
        raise CancelledError()
    if time.monotonic() >= deadline:
        raise TimeoutError("Service request deadline expired")


@contextmanager
def service_connection(descriptor, *, deadline):
    if descriptor.host != local_host():
        raise ProtocolError("Service host identity mismatch")
    validate_endpoint(descriptor.endpoint)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(max(0.001, min(1.0, deadline - time.monotonic())))
        sock.connect(descriptor.endpoint)
        peer_identity(sock, expected_pid=descriptor.pid)
        process = process_identity(descriptor.pid)
        if process["start"] != descriptor.process_start or process["state"] in {"Z", "X"}:
            raise ProtocolError("Service process identity mismatch")
        yield Connection(sock, max_frame=FRAME_LIMIT)
