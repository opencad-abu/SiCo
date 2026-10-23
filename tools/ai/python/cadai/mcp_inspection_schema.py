"""MCP schemas for inspection operations."""

from .inspection import MAX_INSPECT_ITEMS, MAX_MASTER_TARGETS

INSPECT_SCHEMATIC = {
    "name": "inspect_schematic",
    "title": "Inspect schematic",
    "description": (
        "Read bounded instances, nets, and terminals from the active schematic or a named "
        "schematic cellview. This tool never modifies the design."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "lib": {"type": "string", "minLength": 1, "maxLength": 256},
            "cell": {"type": "string", "minLength": 1, "maxLength": 256},
            "view": {"type": "string", "minLength": 1, "maxLength": 256},
            "view_type": {"type": "string", "minLength": 1, "maxLength": 256},
            "include_placement": {"type": "boolean"},
            "max_items": {"type": "integer", "minimum": 1, "maximum": MAX_INSPECT_ITEMS},
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

INSPECT_LAYOUT = {
    "name": "inspect_layout",
    "title": "Inspect layout",
    "description": (
        "Read bounded layout shapes and instances from the active layout or a named layout "
        "cellview. This tool never modifies the design."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "lib": {"type": "string", "minLength": 1, "maxLength": 256},
            "cell": {"type": "string", "minLength": 1, "maxLength": 256},
            "view": {"type": "string", "minLength": 1, "maxLength": 256},
            "view_type": {"type": "string", "minLength": 1, "maxLength": 256},
            "max_items": {"type": "integer", "minimum": 1, "maximum": MAX_INSPECT_ITEMS},
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

INSPECT_SYMBOL_PORTS = {
    "name": "inspect_symbol_ports",
    "title": "Inspect symbol ports",
    "description": (
        "Read bounded terminals and port ordering from the active symbol or a named symbol "
        "cellview. This tool never modifies the design."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "lib": {"type": "string", "minLength": 1, "maxLength": 256},
            "cell": {"type": "string", "minLength": 1, "maxLength": 256},
            "view": {"type": "string", "minLength": 1, "maxLength": 256},
            "view_type": {"type": "string", "minLength": 1, "maxLength": 256},
            "max_items": {"type": "integer", "minimum": 1, "maximum": MAX_INSPECT_ITEMS},
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

INSPECT_MASTER_EVIDENCE = {
    "name": "inspect_master_evidence",
    "title": "Inspect master evidence",
    "description": (
        "Read saved symbol body evidence for up to 8 library/cell/view targets: the view "
        "list, occupied bbox (pins/labels/text excluded), annotation bbox, pin centers "
        "with a suggested outward escape, and a bounded body-signature preview. Read-only; "
        "the capture matches the PDK binding path."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "targets": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_MASTER_TARGETS,
                "items": {
                    "type": "object",
                    "properties": {
                        "lib": {"type": "string", "minLength": 1, "maxLength": 256},
                        "cell": {"type": "string", "minLength": 1, "maxLength": 256},
                        "view": {"type": "string", "minLength": 1, "maxLength": 256},
                    },
                    "required": ["lib", "cell"],
                    "additionalProperties": False,
                },
            },
            "max_items": {"type": "integer", "minimum": 1, "maximum": MAX_INSPECT_ITEMS},
        },
        "required": ["targets"],
        "additionalProperties": False,
    },
    "annotations": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
}

LIST_LIBRARIES = {
    "name": "list_libraries",
    "title": "List libraries",
    "description": "List a bounded set of libraries visible to the current Virtuoso session.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "max_items": {"type": "integer", "minimum": 1, "maximum": MAX_INSPECT_ITEMS}
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

INSPECT_LIBRARY = {
    "name": "inspect_library",
    "title": "Inspect library",
    "description": "Read a library's path and technology binding without changing the library.",
    "inputSchema": {
        "type": "object",
        "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 256}},
        "required": ["name"],
        "additionalProperties": False,
    },
    "annotations": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
}

INSPECT_CONFIG_BINDING = {
    "name": "inspect_config_binding",
    "title": "Inspect config binding",
    "description": (
        "Read a bounded, read-only HDB/ADE configuration summary for an explicit config "
        "cellview. This tool never edits config or ADE state."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "lib": {"type": "string", "minLength": 1, "maxLength": 256},
            "cell": {"type": "string", "minLength": 1, "maxLength": 256},
            "view": {"type": "string", "minLength": 1, "maxLength": 256},
            "max_items": {"type": "integer", "minimum": 1, "maximum": MAX_INSPECT_ITEMS},
        },
        "required": ["lib", "cell", "view"],
        "additionalProperties": False,
    },
    "annotations": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
}
