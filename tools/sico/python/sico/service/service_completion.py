"""Local polling completion; cancelling/discarding never claims remote rollback."""

import threading
from concurrent.futures import CancelledError


class ServiceCompletion:
    def __init__(self, waiter, detached):
        self.__waiter = waiter
        self.__detached = detached
        self.__discarded = threading.Event()

    def done(self):
        return self.cancelled() or self.__waiter.ready.done()

    def cancelled(self):
        return (self.__discarded.is_set() or self.__detached.is_set()
                or self.__waiter.ready.cancelled())

    def cancel(self):
        return self.__waiter.ready.cancel()

    def discard(self):
        """Also hide an already-published result after a page activation is retired."""
        self.__discarded.set()
        self.cancel()

    def result(self, timeout=None):
        """Qt calls only after done(); backend tests may wait with a deadline."""
        if self.cancelled():
            raise CancelledError()
        value = self.__waiter.ready.result(timeout)
        if self.cancelled():
            raise CancelledError()
        return value
