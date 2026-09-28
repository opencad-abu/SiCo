"""Single listener-owned authority for admission, pending work and idle shutdown."""

import math
import time
from dataclasses import asdict, dataclass

DEFAULT_IDLE_SECONDS = 300.0


@dataclass(frozen=True)
class PendingWork:
    sessions: int = 0
    queued: int = 0
    approvals: int = 0
    jobs: int = 0
    reconcile: int = 0

    def __post_init__(self):
        if any(type(value) is not int or not 0 <= value <= 2**31 - 1
               for value in asdict(self).values()):
            raise ValueError("Invalid pending service work counts")

    @property
    def empty(self):
        return not any(asdict(self).values())


class ServiceLifecycle:
    """All calls run on the listener. pending() supplies a bounded committed view, no I/O."""

    def __init__(self, *, idle_seconds=DEFAULT_IDLE_SECONDS, pending=PendingWork,
                 clock=time.monotonic):
        if not math.isfinite(idle_seconds) or idle_seconds <= 0:
            raise ValueError("Service idle timeout must be finite and positive")
        self._idle_seconds, self._pending, self._clock = idle_seconds, pending, clock
        self._idle_since = clock()
        self._reason = None

    @property
    def stopping(self):
        return self._reason is not None

    def _work(self, now):
        work = self._pending()
        if not isinstance(work, PendingWork):
            raise TypeError("Expected pending service work snapshot")
        if not work.empty:
            self._idle_since = None
        elif self._idle_since is None:
            self._idle_since = now
        return work

    def attach(self):
        if self.stopping:
            return False
        # A validated hello and idle transition share this authority/event loop.
        now = self._clock()
        if self._work(now).empty:
            self._idle_since = now
        return True

    def tick(self, *, attaching=False):
        now = self._clock()
        if not self.stopping and attaching:
            self.attach()
            return
        if (not self.stopping and self._work(now).empty
                and now - self._idle_since >= self._idle_seconds):
            self._reason = "idle"

    def stop(self, reason="requested"):
        now = self._clock()
        work = self._work(now)
        if not self.stopping and work.empty:
            self._reason = reason
        return self._snapshot(work, now)

    def signal_shutdown(self):
        """Begin bounded process shutdown even when live sessions remain."""
        now = self._clock()
        work = self._work(now)
        if not self.stopping:
            self._reason = "signal"
        return self._snapshot(work, now)

    def status(self):
        now = self._clock()
        return self._snapshot(self._work(now), now)

    def _snapshot(self, work, now):
        remaining = None
        if not self.stopping and self._idle_since is not None:
            remaining = max(0.0, self._idle_seconds - (now - self._idle_since))
        return dict(state="stopping" if self.stopping else "ready", reason=self._reason,
                    pending=asdict(work), idle_remaining=remaining)
