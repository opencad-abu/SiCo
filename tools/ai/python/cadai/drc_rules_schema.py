"""MCP schema for the read-only DRC rule catalog."""

from .drc_rules import NAME

NAMES = frozenset({NAME})

TOOLS = ({
    "name": NAME,
    "description": "Read DRC rules.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "config_path": {"type": "string"},
            "work_root": {"type": "string"},
            "calibre_root": {"type": "string"},
        },
        "required": ["config_path", "work_root"],
    },
    "annotations": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
},)

__all__ = ["NAMES", "TOOLS"]
