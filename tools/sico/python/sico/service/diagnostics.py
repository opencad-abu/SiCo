"""Bounded, nonblocking diagnostic submission; handlers run on a daemon worker."""

import logging
import queue
import threading


class DiagnosticQueue:
    def __init__(self, capacity=64):
        self.queue = queue.Queue(maxsize=capacity)
        self.dropped = 0
        self.thread = threading.Thread(target=self._run, name="copilot-diagnostics", daemon=True)
        self.thread.start()

    def submit(self, name, message, error=None):
        try:
            self.queue.put_nowait((name, message, error))
        except queue.Full:
            self.dropped += 1

    def _run(self):
        while True:
            name, message, error = self.queue.get()
            try:
                info = (type(error), error, error.__traceback__) if error is not None else None
                logging.getLogger(name).warning(message, exc_info=info)
            except Exception:
                pass
            finally:
                self.queue.task_done()


diagnostics = DiagnosticQueue()
