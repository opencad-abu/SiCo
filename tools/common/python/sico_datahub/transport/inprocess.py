"""Bounded queue transport for SiCo Python and deterministic unit tests."""

from __future__ import annotations

import queue
from typing import Optional

from ..envelope import Envelope
from ..errors import HubError
from .base import Transport, TransportClosed, require_envelope

_CLOSED = object()


class InProcessTransport(Transport):
    def __init__(self, inbox, outbox, identity):
        self._inbox = inbox
        self._outbox = outbox
        self._identity = identity
        self._closed = False

    @classmethod
    def pair(cls, left="inprocess-left", right="inprocess-right"):
        left_inbox, right_inbox = queue.Queue(), queue.Queue()
        return (
            cls(right_inbox, left_inbox, left),
            cls(left_inbox, right_inbox, right),
        )

    def send(self, envelope: Envelope) -> None:
        if self._closed:
            raise TransportClosed("Transport is closed")
        self._outbox.put(Envelope.from_dict(require_envelope(envelope).to_dict()))

    def receive(self, deadline: Optional[float] = None) -> Envelope:
        if self._closed:
            raise TransportClosed("Transport is closed")
        timeout = self.timeout(deadline)
        try:
            value = self._inbox.get(timeout=timeout)
        except queue.Empty as exc:
            raise HubError("timeout", "In-process receive timed out") from exc
        if value is _CLOSED:
            self._inbox.put(_CLOSED)
            raise TransportClosed("Peer transport is closed")
        return value

    def close(self, reason: str = "") -> None:
        if self._closed:
            return
        self._closed = True
        self._outbox.put(_CLOSED)

    def identity(self) -> str:
        return self._identity


__all__ = ["InProcessTransport"]
