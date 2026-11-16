"""Bounded listing for project-level visible-DRC jobs."""

from __future__ import annotations


def _count(value, field, default, minimum, maximum):
    if value is None:
        return default
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return value


def list_payload(registry, arguments):
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    unknown = set(arguments) - {"offset", "limit"}
    if unknown:
        raise ValueError("unknown argument: " + sorted(unknown)[0])
    offset = _count(arguments.get("offset"), "offset", 0, 0, 4096)
    limit = _count(arguments.get("limit"), "limit", 20, 1, 50)
    rows = registry.list_jobs(offset, limit + 1)
    has_more = len(rows) > limit
    return {
        "ok": True,
        "schema": "cad.visible_drc.jobs.v1",
        "jobs": rows[:limit],
        "count": min(len(rows), limit),
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
    }


def service_list_jobs(registry, closed, arguments):
    if closed:
        raise RuntimeError("Visible DRC service is closed")
    if registry is None:
        raise RuntimeError("Visible DRC registry is unavailable")
    return list_payload(registry, arguments)


__all__ = ["list_payload", "service_list_jobs"]
