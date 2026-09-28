"""Immutable observation of the session worker used by command components."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionView:
    session_id: str
    runtime_id: str
    current: object
    busy: bool
    pending: int
    closing: bool
    fault: str
    shutdown: bool
    cancelled: bool
    task: dict
    paused: bool
    version: int

    def require_available(self):
        if self.closing or self.fault or self.shutdown:
            raise ValueError("Session is closing or unavailable")
