"""Apply project-service cancel intents to one MCP-owned visible job."""

from __future__ import annotations

import os
import threading
import time


def start_cancel_watch(service, job_ref):
    thread = threading.Thread(
        target=_run,
        args=(service, job_ref),
        name="visible-drc-cancel-" + job_ref,
        daemon=True,
    )
    thread.start()


def _run(service, job_ref):
    registry = service.registry
    if registry is None:
        return
    try:
        while not service._closed:
            job = service._jobs.get(job_ref)
            if job is None or job.cached is not None:
                return
            try:
                registry.write_lease(job_ref, os.getpid())
            except Exception:
                pass
            try:
                if registry.cancel_requested(job_ref):
                    service.cancel(job_ref)
                    return
            except Exception:
                return
            time.sleep(0.5)
    finally:
        try:
            registry.remove_lease(job_ref)
        except Exception:
            pass


__all__ = ["start_cancel_watch"]
