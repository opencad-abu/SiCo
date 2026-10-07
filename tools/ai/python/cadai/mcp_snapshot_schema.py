"""MCP schemas for snapshot operations."""

from .schematic_snapshot import MAX_QUERY_LIMIT

SNAPSHOT_SCHEMATIC = {
    "name": "snapshot_schematic",
    "title": "Snapshot schematic",
    "description": (
        "Create a complete, immutable JSONL snapshot of the direct schematic instances, "
        "nets, and terminals. The response contains only a snapshot_id and artifact metadata; "
        "query it with query_schematic or get_schematic_item."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "lib": {"type": "string", "minLength": 1, "maxLength": 256},
            "cell": {"type": "string", "minLength": 1, "maxLength": 256},
            "view": {"type": "string", "minLength": 1, "maxLength": 256},
            "view_type": {"type": "string", "minLength": 1, "maxLength": 256},
            "include_placement": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
    "annotations": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
}

QUERY_SCHEMATIC = {
    "name": "query_schematic",
    "title": "Query schematic snapshot",
    "description": (
        "Return one bounded page from a previously created schematic snapshot. Filter by "
        "entity, exact/prefix name, hierarchy path, or connected net."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "snapshot_id": {"type": "string", "pattern": "^snap_[0-9a-f]{64}$"},
            "entity": {"type": "string", "enum": ["all", "instance", "net", "terminal"]},
            "name": {"type": "string", "minLength": 1, "maxLength": 512},
            "name_prefix": {"type": "string", "minLength": 1, "maxLength": 512},
            "hierarchy_path": {"type": "string", "maxLength": 1024},
            "net": {"type": "string", "minLength": 1, "maxLength": 512},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_QUERY_LIMIT},
            "cursor": {"type": "string", "maxLength": 32},
        },
        "required": ["snapshot_id"],
        "additionalProperties": False,
    },
    "annotations": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
}

GET_SCHEMATIC_ITEM = {
    "name": "get_schematic_item",
    "title": "Get schematic item",
    "description": "Return one exact instance, net, or terminal from a schematic snapshot.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "snapshot_id": {"type": "string", "pattern": "^snap_[0-9a-f]{64}$"},
            "entity": {"type": "string", "enum": ["instance", "net", "terminal"]},
            "name": {"type": "string", "minLength": 1, "maxLength": 512},
            "hierarchy_path": {"type": "string", "maxLength": 1024},
        },
        "required": ["snapshot_id", "entity", "name"],
        "additionalProperties": False,
    },
    "annotations": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
}

EXPORT_SCHEMATIC_TEXT = {
    "name": "export_schematic_text",
    "title": "Export schematic text",
    "description": (
        "Export a schematic snapshot as a private tab-separated text artifact for external "
        "search and review. The response contains artifact metadata, not the full body."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "snapshot_id": {"type": "string", "pattern": "^snap_[0-9a-f]{64}$"},
            "entity": {"type": "string", "enum": ["all", "instance", "net", "terminal"]},
            "name_prefix": {"type": "string", "minLength": 1, "maxLength": 512},
        },
        "required": ["snapshot_id"],
        "additionalProperties": False,
    },
    "annotations": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
}
