"""Client for the explicit Unix DataHub endpoint core."""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Optional

from ..codec import decode_line, encode_line
from ..envelope import Envelope, Meta
from ..errors import HubError
from ..limits import MAX_ENVELOPE_BYTES
from ..registry import Registry, default_registry
from .server import HELLO_ACTION, HELLO_CONTRACT, HELLO_SCHEMA


class UnixEnvelopeClient:
    def __init__(
        self,
        path,
        token,
        session_ref,
        *,
        timeout: float = 5.0,
        registry: Optional[Registry] = None,
    ):
        if not isinstance(token, str) or not token:
            raise HubError("invalid_arguments", "Invalid endpoint token")
        if not isinstance(session_ref, str) or not session_ref:
            raise HubError("invalid_arguments", "Invalid endpoint session")
        self.path = Path(path)
        self._token = token
        self.session_ref = session_ref
        self.timeout = timeout
        self._registry = registry or default_registry()
        self._socket = None
        self._stream = None

    def connect(self):
        if self._socket is not None:
            return self
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(str(self.path))
        except OSError as exc:
            sock.close()
            raise HubError("capability_not_available", "Endpoint is unavailable") from exc
        self._socket = sock
        self._stream = sock.makefile("rwb", buffering=0)
        hello = Envelope(
            contract=HELLO_CONTRACT,
            version=1,
            action=HELLO_ACTION,
            id="client-hello",
            payload={"token": self._token, "session_ref": self.session_ref},
            meta=Meta(source="python", target="python"),
        )
        self._registry.validate_request(hello, capabilities={"datahub.endpoint"})
        response = self._exchange(hello)
        if response.status != "ok":
            self.close()
            error = response.error
            raise HubError(
                error.code if error is not None else "capability_not_available",
                error.message if error is not None else "Endpoint session rejected",
            )
        if response.payload.get("schema") != HELLO_SCHEMA:
            self.close()
            raise HubError("invalid_arguments", "Endpoint hello schema mismatch")
        return self

    def _exchange(self, envelope):
        try:
            self._stream.write(encode_line(envelope))
            line = self._stream.readline(MAX_ENVELOPE_BYTES + 2)
        except socket.timeout as exc:
            raise HubError("timeout", "Endpoint exchange timed out") from exc
        except OSError as exc:
            raise HubError("read_failed", "Endpoint exchange failed") from exc
        if not line:
            raise HubError("read_failed", "Endpoint closed without a response")
        response = decode_line(line)
        if (
            response.contract != envelope.contract
            or response.version != envelope.version
            or response.action != envelope.action
            or response.id != envelope.id
        ):
            raise HubError("invalid_arguments", "Endpoint response identity mismatch")
        return response

    def exchange(self, envelope: Envelope) -> Envelope:
        if not isinstance(envelope, Envelope):
            raise HubError("invalid_arguments", "Expected a DataHub envelope")
        if self._stream is None:
            raise HubError("capability_not_available", "Endpoint client is not connected")
        self._registry.validate_request(envelope)
        return self._exchange(envelope)

    def close(self, reason: str = ""):
        if self._stream is not None:
            try:
                self._stream.close()
            except OSError:
                pass
            self._stream = None
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *_args):
        self.close()


__all__ = ["UnixEnvelopeClient"]
