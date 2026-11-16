"""Unix-socket transport using the existing strict length-prefixed framing."""

from __future__ import annotations

from typing import Optional

from ..envelope import Envelope
from ..errors import HubError
from ..limits import MAX_ENVELOPE_BYTES
from .base import Transport, TransportClosed, require_envelope


class UnixTransport(Transport):
    def __init__(self, sock, *, identity="unix", max_bytes=MAX_ENVELOPE_BYTES):
        from sico.transport.framing import Connection

        self._socket = sock
        self._identity = identity
        self._connection = Connection(sock, max_frame=max_bytes)
        self._closed = False

    def send(self, envelope: Envelope) -> None:
        if self._closed:
            raise TransportClosed("Transport is closed")
        try:
            self._connection.send(require_envelope(envelope).to_dict())
        except (OSError, EOFError) as exc:
            raise TransportClosed("Unix peer disconnected during send") from exc
        except (ValueError, UnicodeError) as exc:
            raise HubError("invalid_arguments", "Unix frame could not be encoded") from exc

    def receive(self, deadline: Optional[float] = None) -> Envelope:
        if self._closed:
            raise TransportClosed("Transport is closed")
        try:
            value = self._connection.receive(deadline=deadline)
        except TimeoutError as exc:
            raise HubError("timeout", "Unix receive timed out") from exc
        except (EOFError, OSError) as exc:
            raise TransportClosed("Unix peer disconnected during receive") from exc
        except (ValueError, UnicodeError) as exc:
            raise HubError("invalid_arguments", "Unix peer sent an invalid frame") from exc
        return Envelope.from_dict(value)

    def close(self, reason: str = "") -> None:
        if self._closed:
            return
        self._closed = True
        self._connection.close()

    def identity(self) -> str:
        return self._identity


__all__ = ["UnixTransport"]
