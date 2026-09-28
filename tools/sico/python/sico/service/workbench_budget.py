"""Cooperative CPU budget for Python indexing and queries alongside the Qt thread."""

import time


class WorkbenchBudget:
    def __init__(self, validate=lambda: None):
        self.validate = validate
        self.deadline = time.monotonic() + .004

    def checkpoint(self):
        if time.monotonic() >= self.deadline:
            self.validate()
            # A worker can repeatedly reacquire the GIL ahead of Qt callbacks.
            # Yield between rows, never while holding a journal or queue lock.
            time.sleep(.001)
            self.deadline = time.monotonic() + .004

    def rows(self, rows):
        for row in rows:
            self.checkpoint()
            yield row
