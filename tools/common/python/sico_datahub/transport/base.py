"""One transport boundary for in-process, stdio, Unix and subprocess peers."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Optional

from ..envelope import Envelope
from ..errors import HubError


class TransportClosed(EOFError):
    """The peer or local endpoint can no longer exchange envelopes."""


class Transport(ABC):
    @abstractmethod
    def send(self, envelope: Envelope) -> None:
        """Send one already-validated envelope."""

    @abstractmethod
    def receive(self, deadline: Optional[float] = None) -> Envelope:
        """Receive one envelope before an absolute monotonic deadline."""

    @abstractmethod
    def close(self, reason: str = "") -> None:
        """Close this endpoint without interpreting any domain payload."""

    @abstractmethod
    def identity(self) -> str:
        """Return a stable endpoint identity for diagnostics and routing."""

    @staticmethod
    def timeout(deadline: Optional[float]) -> Optional[float]:
        if deadline is None:
            return None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise HubError("timeout", "Transport receive deadline expired")
        return remaining


def require_envelope(value):
    if not isinstance(value, Envelope):
        raise HubError("invalid_arguments", "Transport accepts DataHub envelopes only")
    return value


__all__ = ["Transport", "TransportClosed", "require_envelope"]
