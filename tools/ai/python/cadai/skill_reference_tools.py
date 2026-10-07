"""Dependency-free contracts for the offline Cadence SKILL reference."""

from __future__ import annotations

from sicoenv import read as environment_setting

import os
from collections.abc import Mapping
from pathlib import Path
from sicosessionenv import value as session_value
from .optional_resources import resource_path

MAX_QUERY_CHARS = 128
MAX_SEARCH_RESULTS = 10
MAX_EXACT_MATCHES = 10
MAX_DOCUMENT_CHARS = 65_536
SKILL_REFERENCE_TOOL_NAMES = frozenset({"search_skill_api", "get_skill_api"})


def normalize_reference_root(value: str, *, workspace=None) -> Path:
    return resource_path(value, "SICO_AI_SKILL_REFERENCE_ROOT", workspace=workspace)


def default_reference_root(environment: Mapping[str, str] | None = None) -> Path:
    source = os.environ if environment is None else environment
    configured = environment_setting(source, "SICO_AI_SKILL_REFERENCE_ROOT", "").strip()
    if configured:
        return normalize_reference_root(configured, workspace=session_value(source, "WORKSPACE") or None)
    return Path(__file__).resolve().parents[2] / "reference" / "cadence-skill"

_READ_ONLY_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

SKILL_REFERENCE_TOOLS = [
    {
        "name": "search_skill_api",
        "title": "Search Cadence SKILL APIs",
        "description": (
            "Search the bundled offline Cadence SKILL reference by function name, signature, "
            "or documentation text. Use get_skill_api on a returned exact name before writing "
            "or evaluating unfamiliar SKILL."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": MAX_QUERY_CHARS},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_SEARCH_RESULTS,
                    "default": 5,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "annotations": dict(_READ_ONLY_ANNOTATIONS),
    },
    {
        "name": "get_skill_api",
        "title": "Get a Cadence SKILL API reference",
        "description": (
            "Read the bundled signature and compact documentation for an exact Cadence SKILL "
            "function name or reference id. This tool never executes SKILL."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": MAX_QUERY_CHARS},
                "max_chars": {
                    "type": "integer",
                    "minimum": 1_024,
                    "maximum": MAX_DOCUMENT_CHARS,
                    "default": MAX_DOCUMENT_CHARS,
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        "annotations": dict(_READ_ONLY_ANNOTATIONS),
    },
]

__all__ = [
    "MAX_DOCUMENT_CHARS",
    "MAX_EXACT_MATCHES",
    "MAX_QUERY_CHARS",
    "MAX_SEARCH_RESULTS",
    "SKILL_REFERENCE_TOOL_NAMES",
    "SKILL_REFERENCE_TOOLS",
    "default_reference_root",
    "normalize_reference_root",
]
