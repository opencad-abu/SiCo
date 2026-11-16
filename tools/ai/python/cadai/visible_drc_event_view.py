"""Attach durable visible-DRC state events to service payloads."""

from __future__ import annotations


def remember_state(registry, job_ref, state, message=""):
    if registry is None:
        return
    try:
        registry.update_state(job_ref, state)
    except Exception:
        pass
    try:
        registry.append_event(job_ref, state, message)
    except Exception:
        pass


def discard_job(registry, job_ref):
    if registry is None:
        return
    try:
        registry.remove(job_ref)
    except Exception:
        pass


def cancel_requested(registry, job_ref):
    if registry is None:
        return False
    try:
        return registry.cancel_requested(job_ref)
    except Exception:
        return False


def clear_cancel(registry, job_ref):
    if registry is None:
        return
    try:
        registry.clear_cancel(job_ref)
    except Exception:
        pass


def with_events(registry, payload, job_ref, after=0):
    if registry is None:
        return payload
    try:
        events = registry.read_events(job_ref, after, 32)
    except Exception:
        events = []
    payload["events"] = events
    payload["event_cursor"] = events[-1]["seq"] if events else after
    return payload


__all__ = [
    "cancel_requested",
    "clear_cancel",
    "discard_job",
    "remember_state",
    "with_events",
]
