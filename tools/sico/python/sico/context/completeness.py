"""Per-section completeness for bounded model context."""

from __future__ import annotations

STATES = frozenset({"complete", "summary", "truncated", "partial", "unknown", "stale"})
EXPANDS = frozenset({"inspect", "snapshot", "none"})


def section(returned, total, *, expand="none", next_action="", detail_omitted=False,
            stale=False, available=True):
    """Describe one returned collection without pretending it is complete."""
    if type(returned) is not int or returned < 0:
        raise ValueError("returned must be a non-negative integer")
    if total is not None and (type(total) is not int or total < 0):
        raise ValueError("total must be a non-negative integer or null")
    if total is not None and returned > total:
        raise ValueError("returned cannot exceed total")
    if expand not in EXPANDS:
        raise ValueError("invalid completeness expand mode")
    if not isinstance(next_action, str):
        raise ValueError("next_action must be a string")
    if not isinstance(detail_omitted, bool) or not isinstance(stale, bool):
        raise ValueError("completeness flags must be boolean")
    if not available:
        state = "partial"
        omitted = None if total is None else max(0, total - returned)
    elif stale:
        state = "stale"
        omitted = None if total is None else max(0, total - returned)
    elif total is None:
        state = "unknown"
        omitted = None
    elif total == returned:
        state = "summary" if detail_omitted else "complete"
        omitted = 0
    else:
        state = "truncated"
        omitted = total - returned
    if not next_action:
        next_action = {
            "truncated": "expand",
            "partial": "ask_user",
            "unknown": "ask_user",
            "stale": "re_read",
        }.get(state, "")
    return {
        "state": state,
        "returned": returned,
        "total": total,
        "omitted": omitted,
        "expand": expand,
        "next_action": next_action,
    }


def aggregate(sections):
    """Aggregate section states; incomplete always wins over complete."""
    if not isinstance(sections, dict) or not sections:
        raise ValueError("sections must be a non-empty object")
    values = []
    for name, value in sections.items():
        if not isinstance(name, str) or not isinstance(value, dict):
            raise ValueError("invalid completeness section")
        state = value.get("state")
        if state not in STATES:
            raise ValueError("invalid completeness state")
        values.append(state)
    if "stale" in values:
        state = "stale"
    elif "partial" in values or "unknown" in values:
        state = "partial"
    elif "truncated" in values:
        state = "truncated"
    elif "summary" in values:
        state = "summary"
    else:
        state = "complete"
    return {"state": state, "sections": {name: dict(value) for name, value in sections.items()}}
