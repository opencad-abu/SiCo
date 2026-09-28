"""Compact, detached presentation of one session's live Virtuoso routing state."""

from __future__ import annotations

from copy import deepcopy

REQUEST_FIELDS = (
    "request_id", "session_id", "task_id", "tool_call_id", "target_id", "method",
    "state", "accepted_at", "queued_at", "started_at", "queue_position", "blocked_by",
    "cancel_requested_at",
    "queue_deadline", "execution_deadline",
)


def router_status(snapshot, context, session_id):
    status = {
        "instance_id": context.instance_id,
        "generation": context.generation,
        "target_id": context.target_id,
        "router_id": None,
        "bridge_id": None,
        "state": "router_unavailable",
        "queued": 0,
        "owner": None,
        "unknown_since": None,
        "requests": [],
    }
    if (snapshot is None or snapshot["instance_id"] != context.instance_id
            or snapshot["generation"] != context.generation):
        return status
    status.update(router_id=snapshot["router_id"], queued=snapshot["queued"],
                  unknown_since=snapshot["unknown_since"], bridge_id=snapshot.get("bridge_id"))
    if snapshot["closed"]:
        return status
    owner = snapshot["unknown_request"] or snapshot["active_request"]
    status["state"] = (
        "blocked_unknown" if snapshot["unknown"] else "running" if snapshot["active"]
        else "queued" if snapshot["queued"] else "idle"
    )

    def row(value):
        return {key: deepcopy(value[key]) for key in REQUEST_FIELDS if key in value}

    if owner:
        status["owner"] = row(owner)
    for request in ([owner] if owner else []) + snapshot["queue"]:
        if (request.get("session_id") == session_id
                and request.get("target_id") == context.target_id):
            status["requests"].append(row(request))
    return status
