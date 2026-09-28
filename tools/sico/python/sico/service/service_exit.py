"""Final process deadline; the OS releases the project lease after all threads die."""

import os
import threading

from .cleanup_task import SERVICE_EXIT_SECONDS


class ServiceExit:
    def __init__(self, stopped, seconds=SERVICE_EXIT_SECONDS):
        self.stopped, self.seconds = stopped, seconds
        self.finished = threading.Event()
        self._thread = threading.Thread(target=self._watch, name="copilot-exit", daemon=True)
        self._thread.start()

    def _watch(self):
        self.stopped.wait()
        if not self.finished.wait(self.seconds):
            # No atexit joins, no premature lease release, no fabricated completion.
            # Persisted inbox/journal/end intent remain authoritative after restart.
            os._exit(3)

    def close(self):
        self.finished.set()
        self.stopped.set()
        self._thread.join(.1)
