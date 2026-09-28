"""Nonblocking desktop shutdown and its single Qt grace deadline."""

import time

from sico.service.diagnostics import diagnostics


class CloseLifecycle:
    GRACE_SECONDS = 5.0

    def __init__(self, api, *, persistent=False, notice=lambda kind: None,
                 clock=time.monotonic):
        self.api = api
        self.persistent = persistent
        self.notice = notice
        self.clock = clock
        self.guard = None
        self.closing = False
        self.deadline = None
        self.forced = False

    def attach_guard(self, guard):
        self.guard = guard
        if self.closing:
            guard.arm()

    def request(self):
        if self.guard is not None:
            self.guard.arm()
        if not self.closing:
            self.closing = True
            self.deadline = self.clock() + self.GRACE_SECONDS
        self.api.close_desktop()

    def expired(self):
        if self.deadline is None or self.clock() < self.deadline:
            return False
        if not self.forced:
            self.forced = True
            diagnostics.submit(__name__,
                "Silicon Copilot Qt close grace expired with unfinished background work")
        return True

    def ready(self):
        return self.closing and (not self.api.desktop_busy or self.expired())
