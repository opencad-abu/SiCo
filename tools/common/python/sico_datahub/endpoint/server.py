"""Explicit-lifetime Unix endpoint core; the host owns its process lifecycle."""

from __future__ import annotations

import hmac
import socket
import threading
from pathlib import Path
from typing import Callable, Mapping, Optional

from ..codec import decode_line, encode_line
from ..envelope import Envelope, Meta
from ..errors import HubError
from ..limits import MAX_ENVELOPE_BYTES
from ..registry import Registry, default_registry

HELLO_CONTRACT = "sico.datahub.hello.v1"
HELLO_ACTION = "hello"
HELLO_CAPABILITY = "datahub.endpoint"
HELLO_SCHEMA = "datahub.hello.v1"
Handler = Callable[[Envelope], Envelope]


class UnixEnvelopeServer:
    """One explicitly started Unix endpoint, never a background daemon."""

    def __init__(
        self,
        path,
        token,
        session_ref,
        handlers: Mapping,
        *,
        registry: Optional[Registry] = None,
    ):
        self.path = Path(path)
        self._token = self._text(token, "endpoint token", 256)
        self.session_ref = self._text(session_ref, "session reference", 128)
        self._registry = registry or default_registry()
        self._handlers = dict(handlers)
        self._capabilities = set()
        for key, handler in self._handlers.items():
            if (
                not isinstance(key, tuple) or len(key) != 2
                or not all(isinstance(value, str) for value in key)
                or not callable(handler)
            ):
                raise HubError("invalid_arguments", "Invalid endpoint handler")
            _, spec = self._registry.action(key[0], 1, key[1])
            self._capabilities.add(spec.capability)
        self._socket = None
        self._thread = None
        self._closed = threading.Event()
        self._connections = set()
        self._connection_lock = threading.Lock()

    @staticmethod
    def _text(value, field, limit):
        if not isinstance(value, str) or not value or len(value) > limit:
            raise HubError("invalid_arguments", "Invalid " + field)
        return value

    @property
    def capabilities(self):
        return tuple(sorted(self._capabilities))

    def start(self):
        if self._socket is not None:
            return self
        if not self.path.parent.is_dir():
            raise HubError("invalid_arguments", "Endpoint parent directory is missing")
        if self.path.exists():
            raise HubError("invalid_arguments", "Endpoint path already exists")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(str(self.path))
            self.path.chmod(0o600)
            sock.listen(8)
            sock.settimeout(0.2)
        except OSError:
            sock.close()
            raise
        self._socket = sock
        self._thread = threading.Thread(target=self._serve, name="sico-datahub-endpoint")
        self._thread.start()
        return self

    def _serve(self):
        while not self._closed.is_set():
            try:
                connection, _address = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            connection.settimeout(30.0)
            thread = threading.Thread(
                target=self._handle,
                args=(connection,),
                name="sico-datahub-connection",
            )
            with self._connection_lock:
                self._connections.add(connection)
            thread.start()

    def _handle(self, connection):
        stream = connection.makefile("rwb", buffering=0)
        hello = None
        try:
            hello = decode_line(stream.readline(MAX_ENVELOPE_BYTES + 2))
            response = self._handshake(hello)
            stream.write(encode_line(response))
            if response.status != "ok":
                return
            while not self._closed.is_set():
                line = stream.readline(MAX_ENVELOPE_BYTES + 2)
                if not line:
                    return
                request = decode_line(line)
                stream.write(encode_line(self._dispatch(request)))
        except (EOFError, OSError):
            return
        except HubError as exc:
            if hello is not None:
                try:
                    stream.write(encode_line(Envelope.from_exception(
                        hello.contract, hello.action, hello.id, exc
                    )))
                except OSError:
                    pass
        finally:
            try:
                stream.close()
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass
            with self._connection_lock:
                self._connections.discard(connection)

    def _handshake(self, request):
        self._registry.validate_request(request, capabilities={HELLO_CAPABILITY})
        if (
            request.contract != HELLO_CONTRACT
            or request.action != HELLO_ACTION
            or request.status != "ok"
        ):
            raise HubError("capability_not_available", "Endpoint session rejected")
        token = request.payload.get("token")
        session_ref = request.payload.get("session_ref")
        if (
            not isinstance(token, str)
            or not hmac.compare_digest(token, self._token)
            or session_ref != self.session_ref
        ):
            raise HubError("capability_not_available", "Endpoint session rejected")
        return Envelope(
            contract=HELLO_CONTRACT,
            version=1,
            action=HELLO_ACTION,
            id=request.id,
            payload={
                "schema": HELLO_SCHEMA,
                "session_ref": self.session_ref,
                "capabilities": list(self.capabilities),
            },
            meta=Meta(source="python", target="python"),
        )

    def _dispatch(self, request):
        try:
            if request.status != "ok" or request.error is not None or request.page is not None:
                raise HubError("invalid_arguments", "Endpoint accepts request envelopes only")
            handler = self._handlers.get((request.contract, request.action))
            if handler is None:
                raise HubError("capability_not_available", "Endpoint action is unavailable")
            self._registry.validate_request(request, capabilities=self._capabilities)
            response = handler(request)
            if not isinstance(response, Envelope):
                raise HubError("internal", "Endpoint handler returned no envelope")
            if (
                response.contract != request.contract
                or response.version != request.version
                or response.action != request.action
                or response.id != request.id
            ):
                raise HubError("internal", "Endpoint response identity mismatch")
            return response
        except HubError as exc:
            return Envelope.from_exception(
                request.contract, request.action, request.id, exc
            )
        except Exception:
            return Envelope.failure(
                request.contract, request.action, request.id, "internal",
                "Endpoint handler failed",
            )

    def close(self, reason: str = ""):
        if self._closed.is_set():
            return
        self._closed.set()
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        with self._connection_lock:
            connections = list(self._connections)
        for connection in connections:
            try:
                connection.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self.path.exists():
            try:
                self.path.unlink()
            except OSError:
                pass

    def __enter__(self):
        return self.start()

    def __exit__(self, *_args):
        self.close()


__all__ = [
    "HELLO_ACTION",
    "HELLO_CAPABILITY",
    "HELLO_CONTRACT",
    "HELLO_SCHEMA",
    "Handler",
    "UnixEnvelopeServer",
]
