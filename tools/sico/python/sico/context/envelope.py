"""Build the context envelope that records sources and budgets."""

from __future__ import annotations

from .contracts import ENVELOPE_SCHEMA, canonical_bytes, sha256_json, string


def build_envelope(payload, *, turn_id, session_id, project_id="", layers=None,
                   selection=None, budget=None, excluded=None, created_at=None):
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    for value, name in ((turn_id, "turn_id"), (session_id, "session_id")):
        string(value, name, allow_empty=False)
    string(project_id, "project_id")
    layers = list(layers or [])
    selection = dict(selection or {"rules": [], "skills": [], "subagents": [], "reason": "shadow"})
    excluded = list(excluded or [])
    if budget is None:
        budget = {
            "bytes": len(canonical_bytes(payload)),
            "tokens": None,
            "tool_calls": 0,
            "truncated": [],
        }
    if not isinstance(budget, dict):
        raise ValueError("budget must be an object")
    target = payload.get("active_target") if isinstance(payload.get("active_target"), dict) else None
    return {
        "schema_version": ENVELOPE_SCHEMA,
        "envelope_id": "ctxenv_" + sha256_json(payload)[:16],
        "turn_step": payload.get("turn_step"),
        "turn_id": turn_id,
        "session_id": session_id,
        "project_id": project_id,
        "target": target,
        "layers": layers,
        "selection": selection,
        "budget": budget,
        "excluded": excluded,
        "created_at": created_at or payload.get("captured_at") or "",
    }
