"""Immutable lifecycle response DTO; the sole status/stop snapshot validator."""

import sys
from dataclasses import asdict, dataclass
from typing import Optional

from ..transport.framing import ProtocolError
from .service_allocation import Allocation
from .service_lifecycle import PendingWork
from .service_protocol import exact_fields


@dataclass(frozen=True)
class ServiceStatus:
    state: str
    reason: Optional[str]
    pending: PendingWork
    idle_remaining: Optional[float]
    allocation: Allocation
    outcome: str

    def record(self):
        return asdict(self)

    @classmethod
    def from_record(cls, reply, method):
        exact_fields(reply, {"state", "reason", "pending", "idle_remaining",
                             "allocation", "outcome"})
        if (method not in ("service.status", "service.stop")
                or reply["state"] not in ("ready", "stopping")
                or reply["reason"] not in (None, "requested", "idle", "signal")):
            raise ProtocolError("Invalid service status response")
        remaining = reply["idle_remaining"]
        if remaining is not None and (type(remaining) not in (int, float)
                                      or not 0 <= remaining <= sys.float_info.max):
            raise ProtocolError("Invalid service idle deadline")
        exact_fields(reply["pending"], {"sessions", "queued", "approvals", "jobs", "reconcile"})
        exact_fields(reply["allocation"], {"job_id", "cluster", "queue"})
        try:
            work = PendingWork(**reply["pending"])
            allocation = Allocation(**reply["allocation"])
        except (TypeError, ValueError) as exc:
            raise ProtocolError("Invalid service status snapshot") from exc
        if ((reply["state"] == "stopping") != (reply["reason"] is not None)
                or (reply["state"] == "stopping" and remaining is not None)):
            raise ProtocolError("Inconsistent service status snapshot")
        outcome = "observed" if method == "service.status" else (
            "stop_requested" if reply["state"] == "stopping" else "blocked")
        if (reply["outcome"] != outcome or (outcome == "blocked" and work.empty)
                or (reply["state"] == "ready" and work.empty != (remaining is not None))):
            raise ProtocolError("Inconsistent service stop outcome")
        return cls(reply["state"], reply["reason"], work, remaining, allocation, outcome)
