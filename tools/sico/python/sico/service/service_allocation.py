"""Capture bounded scheduler attribution without persisting the process environment."""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Allocation:
    job_id: Optional[str] = None
    cluster: Optional[str] = None
    queue: Optional[str] = None

    def __post_init__(self):
        for value in (self.job_id, self.cluster, self.queue):
            if value is not None and (not isinstance(value, str) or not 0 < len(value) <= 128
                                      or not value.isprintable()):
                raise ValueError("Invalid service allocation attribution")


def capture_allocation(environment=None):
    env = os.environ if environment is None else environment
    return Allocation(env.get("LSB_JOBID") or None, env.get("LSF_CLUSTER_NAME") or None,
                      env.get("LSB_QUEUE") or None)
