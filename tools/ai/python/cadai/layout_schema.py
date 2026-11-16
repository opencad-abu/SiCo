"""Draft MCP schemas for the layout backend surface.

These definitions are intentionally not registered in mcp_definitions until
the catalog budget ledger and profile contracts are synchronized.
"""

from .circuit_schema import string_schema, tool

_BACKEND_ID = string_schema(128, pattern=r"^[a-z][a-z0-9_.-]{0,127}$")
_JOB_ID = string_schema(128, pattern=r"^layout-[a-f0-9]{32}$")
_PLAN_FILE = string_schema(4096, pattern=r"^/.*")
_REQUEST = {
    "type": "object",
    "properties": {
        "action": string_schema(64),
        "source": {"type": "object"},
        "process_view_ref": string_schema(256),
        "options": {"type": "object"},
    },
    "required": ["action", "source"],
    "additionalProperties": True,
}

LAYOUT_TOOLS = [
    tool(
        "list_layout_backends",
        "List project layout backends, their capabilities and discovery errors.",
        {},
    ),
    tool(
        "preview_layout_job",
        "Validate a layout job request against one selected backend before starting it.",
        {"backend_id": _BACKEND_ID, "request": _REQUEST},
        ["backend_id", "request"],
    ),
    tool(
        "start_layout_job",
        "Start one asynchronous layout job in an isolated workspace.",
        {"backend_id": _BACKEND_ID, "request": _REQUEST},
        ["backend_id", "request"],
        mutating=True,
    ),
    tool(
        "get_layout_job",
        "Read one layout job; collect and verify artifacts after success.",
        {"job_id": _JOB_ID},
        ["job_id"],
    ),
    tool(
        "cancel_layout_job",
        "Cancel one layout job and preserve its evidence.",
        {"job_id": _JOB_ID},
        ["job_id"],
        mutating=True,
    ),
    tool(
        "plan_layout_import",
        "Build a reviewed XStream-In import plan; this does not write OA.",
        {
            "job_id": _JOB_ID,
            "target_library": string_schema(128, pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.$@+-]{0,127}$"),
            "target_cell": string_schema(128, pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.$@+-]{0,127}$"),
            "target_view": string_schema(128, pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.$@+-]{0,127}$"),
            "layer_map": string_schema(4096, pattern=r"^/.*"),
        },
        ["job_id", "target_library", "target_cell", "target_view", "layer_map"],
        mutating=True,
    ),
    tool(
        "get_layout_import_status",
        "Read layout import completion evidence without executing or writing OA.",
        {"plan_file": _PLAN_FILE},
        ["plan_file"],
    ),
]
LAYOUT_NAMES = frozenset(row["name"] for row in LAYOUT_TOOLS)
