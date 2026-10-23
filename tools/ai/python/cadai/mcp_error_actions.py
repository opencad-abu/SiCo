"""Canonical fallback next actions for common MCP failure codes."""

from __future__ import annotations

GENERIC = (
    "Resolve the condition named in message before retrying; "
    "do not repeat the unchanged call"
)

_ACTIONS = {
    "selection_required": (
        "Ask the user to answer selection_question, then resend with the "
        "selected value and required confirmation"
    ),
    "needs_selection": (
        "Ask the user to answer selection_question, then resend with the "
        "selected value and required confirmation"
    ),
    "response_too_large": (
        "Request a smaller page or scope, or read the returned artifact, "
        "instead of repeating the same bounds"
    ),
    "result_too_large": (
        "Request fewer rows or a smaller scope, or use the returned artifact "
        "path, instead of repeating the same bounds"
    ),
    "payload_too_large": (
        "Shorten or split the submitted source before calling again"
    ),
    "bridge_unavailable": (
        "Verify the SiCo service connection and whether a write already "
        "started; do not repeat a write until its effect is known"
    ),
    "snapshot_not_found": (
        "Use a snapshot_id returned by snapshot_schematic; do not invent or "
        "reuse an expired identifier"
    ),
    "invalid_result": (
        "Treat this as a backend protocol failure, preserve the evidence, "
        "and do not retry the same request unchanged"
    ),
}


def next_action(code: str) -> str:
    """Return the reviewed action for one failure code."""
    return _ACTIONS.get(code, GENERIC)


__all__ = ["GENERIC", "next_action"]
