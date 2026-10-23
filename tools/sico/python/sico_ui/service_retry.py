"""Bound automatic recovery until a connection stays healthy or the user retries."""

import time


class ServiceRetry:
    def __init__(self, *, limit, delay, maximum=30, stable_seconds=30,
                 immediate=True, clock=time.monotonic):
        self.limit, self.delay, self.maximum = limit, delay, maximum
        self.stable_seconds, self.clock = stable_seconds, clock
        self.reset(immediate=immediate)

    @property
    def exhausted(self):
        return self.attempts >= self.limit

    def reset(self, *, immediate=True):
        self.attempts = 0
        self.due = self.clock() + (0 if immediate else self.delay)
        self._stable_since = None

    def start(self):
        """Consume one attempt only after its delay and within the shared budget."""
        now = self.clock()
        if self.exhausted or now < self.due:
            return False
        self.attempts += 1
        self.due = now + min(self.maximum, self.delay * 2 ** (self.attempts - 1))
        self.unhealthy()
        return True

    def unhealthy(self):
        self._stable_since = None

    def healthy(self):
        now = self.clock()
        if self._stable_since is None:
            self._stable_since = now
        elif now - self._stable_since >= self.stable_seconds:
            self.reset(immediate=False)
            self._stable_since = now
