"""Profile-bound MCP capability policy for managed agent sessions."""

from __future__ import annotations

from collections.abc import Iterable

INTERACTIVE_PROFILE = "interactive"
VERIFICATION_PROFILE = "verification"
SUPPORTED_PROFILES = frozenset({INTERACTIVE_PROFILE, VERIFICATION_PROFILE})

# The verification agent can inspect the saved schematic and delegate one
# bounded AIVW run.  Candidate/revision and evidence APIs will be added to
# this same allowlist as their contracts land; exposing SKILL, layout, or
# generic workflow tools before then would silently bypass the control plane.
VERIFICATION_TOOL_NAMES = frozenset(
    {
        "get_context",
        "run_aivw_recipe",
        "submit_candidate",
        "get_candidate",
        "inspect_schematic",
        "inspect_config_binding",
        "snapshot_schematic",
        "query_schematic",
        "get_schematic_item",
        "export_schematic_text",
        "list_windows",
        "live_model_status",
        "live_model_events",
        "list_project_documents",
        "read_project_document",
        "search_project_documents",
    }
)


def validate_profile(profile: str) -> str:
    if profile not in SUPPORTED_PROFILES:
        supported = ", ".join(sorted(SUPPORTED_PROFILES))
        raise ValueError(f"unsupported agent profile {profile!r}; expected {supported}")
    return profile


def allowed_tool_names(profile: str, all_tools: Iterable[str]) -> tuple[str, ...]:
    validate_profile(profile)
    names = tuple(all_tools)
    if profile == INTERACTIVE_PROFILE:
        return names
    return tuple(name for name in names if name in VERIFICATION_TOOL_NAMES)


__all__ = [
    "INTERACTIVE_PROFILE",
    "VERIFICATION_PROFILE",
    "SUPPORTED_PROFILES",
    "VERIFICATION_TOOL_NAMES",
    "allowed_tool_names",
    "validate_profile",
]
