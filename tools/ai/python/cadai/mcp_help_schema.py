"""Contract for the read-only SiCo capability help tool (``mcp_help``).

The tool explains SiCo's own MCP surface over the same catalog the server
dispatches from: it lists tools globally or inside one task group, ranks task
groups for a task, describes a tool or a group, reads the authoritative
``inputSchema`` by JSON Pointer, shows one group's workflow, and serves the
curated worked examples declared by ``mcp_examples``.  It is deliberately small
and never keeps a second copy of a schema or parameter rule.
"""

MCP_HELP_TOOL = {
    "name": "mcp_help",
    "title": "Read SiCo MCP capabilities and tool contracts",
    "description": (
        "Read the SiCo MCP capabilities of this server. list "
        "(entity=tools|groups) ranks tools or task groups; describe takes a tool or group "
        "(purpose, availability, dispatch, missing, recovery); schema reads a "
        "tool's inputSchema by JSON Pointer; workflow(group) shows hand-off notes "
        "and routes; example returns a curated worked example (tool or group). Bounded read-only "
        "bounded; complete=false marks a partial reply. "
        "A group-scoped miss is not a missing capability. Known tools can be called directly."
    ),
    "inputSchema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "describe", "schema", "workflow", "example"],
                "default": "list",
            },
            "entity": {
                "type": "string",
                "enum": ["tools", "groups"],
                "default": "tools",
                "description": "List only: what to list. groups ranks task groups for query.",
            },
            "query": {
                "type": "string",
                "minLength": 1,
                "maxLength": 256,
                "description": "List only: case-insensitive terms; all must match a tool's "
                               "name/description or a group's id/purpose/keywords.",
            },
            "tool": {
                "type": "string",
                "minLength": 1,
                "maxLength": 96,
                "description": "describe/schema/example: registered or legacy tool name "
                               "(legacy reported as legacy_name).",
            },
            "group": {
                "type": "string",
                "minLength": 1,
                "maxLength": 96,
                "description": "describe/workflow/example: task group id; list: restrict to that "
                               "group. A scoped miss never means the capability is absent.",
            },
            "schema_path": {
                "type": "string",
                "maxLength": 512,
                "description": "schema: RFC 6901 JSON Pointer inside the tool's inputSchema; "
                               "'' or omitted reads the root. Missing pointers are reported, "
                               "never silently widened.",
            },
            "offset": {"type": "integer", "minimum": 0},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "default": 20,
                "description": "Values above 20 are clamped.",
            },
        },
    },
    "annotations": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
}

__all__ = ["MCP_HELP_TOOL"]
