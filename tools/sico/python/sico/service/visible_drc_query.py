"""Read-only project-service view of durable visible-DRC jobs and events."""

from __future__ import annotations

import time
from pathlib import Path

from cadai.visible_drc_list import list_payload
from cadai.visible_drc_registry import VisibleDrcRegistry

_ACTIONS = {"list", "get", "cancel", "wait"}
_FIELDS = {"action", "offset", "limit", "job_ref", "after", "timeout_seconds"}


def _lease_view(registry, job_ref):
    lease = registry.read_lease(job_ref)
    active = bool(lease and time.time() - lease["updated_at"] <= 2.0)
    return lease, active


def _recovery_required(record, lease, active):
    return record["state"] in {"queued", "running"} and lease is not None and not active


def query(project, params, *, requester="", sleep=time.sleep, clock=time.monotonic):
    if not isinstance(params, dict):
        raise ValueError("Visible DRC query params must be an object")
    action = params.get("action")
    if action not in _ACTIONS:
        raise ValueError("Visible DRC query action is invalid")
    unknown = set(params) - _FIELDS
    if unknown:
        raise ValueError("Visible DRC query field is invalid: " + sorted(unknown)[0])
    root = Path(project).expanduser()
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("Visible DRC project is unavailable")
    registry = VisibleDrcRegistry(root)
    if action == "list":
        page = {key: params[key] for key in ("offset", "limit") if key in params}
        result = list_payload(registry, page)
        for row in result["jobs"]:
            lease, active = _lease_view(registry, row["job_ref"])
            row["lease"] = lease
            row["owner_active"] = active
            row["recovery_required"] = _recovery_required(row, lease, active)
        return result
    job_ref = params.get("job_ref")
    after = params.get("after", 0)
    record = registry.load(job_ref)
    if record is None:
        raise ValueError("Visible DRC job is unavailable")
    if action == "cancel":
        lease, active = _lease_view(registry, job_ref)
        requested = record["state"] in {"queued", "running"} and active
        if requested:
            registry.request_cancel(job_ref, requester)
        elif record["state"] in {"queued", "running"}:
            registry.clear_cancel(job_ref)
        events = registry.read_events(job_ref, after, 32)
        return {
            "ok": True,
            "schema": "cad.visible_drc.cancel.v1",
            "job": record,
            "cancel_requested": requested,
            "lease": lease,
            "owner_active": active,
            "recovery_required": _recovery_required(record, lease, active),
            "events": events,
            "event_cursor": events[-1]["seq"] if events else after,
        }
    if action == "wait":
        timeout = params.get("timeout_seconds", 5)
        if type(timeout) not in {int, float} or not 0 < float(timeout) <= 30:
            raise ValueError("Visible DRC wait timeout is invalid")
        deadline = clock() + float(timeout)
        while True:
            lease, active = _lease_view(registry, job_ref)
            events = registry.read_events(job_ref, after, 32)
            if _recovery_required(record, lease, active):
                return {
                    "ok": True,
                    "schema": "cad.visible_drc.events.v1",
                    "job_ref": record["job_ref"],
                    "events": events,
                    "event_cursor": events[-1]["seq"] if events else after,
                    "timed_out": False,
                    "lease": lease,
                    "owner_active": False,
                    "recovery_required": True,
                }
            if events:
                return {
                    "ok": True,
                    "schema": "cad.visible_drc.events.v1",
                    "job_ref": record["job_ref"],
                    "events": events,
                    "event_cursor": events[-1]["seq"],
                    "timed_out": False,
                    "lease": lease,
                    "owner_active": active,
                    "recovery_required": False,
                }
            remaining = deadline - clock()
            if remaining <= 0:
                return {
                    "ok": True,
                    "schema": "cad.visible_drc.events.v1",
                    "job_ref": record["job_ref"],
                    "events": [],
                    "event_cursor": after,
                    "timed_out": True,
                    "lease": lease,
                    "owner_active": active,
                    "recovery_required": _recovery_required(record, lease, active),
                }
            sleep(min(0.1, remaining))
    events = registry.read_events(job_ref, after, 32)
    lease, active = _lease_view(registry, job_ref)
    return {
        "ok": True,
        "schema": "cad.visible_drc.job.v1",
        "job": record,
        "events": events,
        "event_cursor": events[-1]["seq"] if events else after,
        "lease": lease,
        "owner_active": active,
        "recovery_required": _recovery_required(record, lease, active),
    }


__all__ = ["query"]
