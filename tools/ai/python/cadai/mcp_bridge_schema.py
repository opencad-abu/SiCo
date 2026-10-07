"""MCP schemas for bridge operations."""

from .mcp_transport import MAX_EVAL_BYTES

GET_CONTEXT = {
    "name": "get_context",
    "title": "Get Virtuoso context",
    "description": "Read the current cwd, editor window, cellview, and selection count.",
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    "annotations": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
}

EVAL_SKILL = {
    "name": "eval_skill",
    "title": "Evaluate one SKILL expression",
    "description": (
        "Evaluate exactly one classic Cadence SKILL expression in the current "
        "Virtuoso process. Returns value and synchronous poport/woport/errport "
        "text in output, including buffered warnings and errors, with "
        "output_capture=skill_ports and *_truncated flags. Read this reply "
        "instead of polling CDS.log; legacy Assistant errors use data.output, "
        "and spooled results use artifact.path. Does not collect CIW history or later "
        "asynchronous output. "
        "For multiple forms, write a .il/.ils file and use load_skill_file."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_EVAL_BYTES,
            }
        },
        "required": ["code"],
        "additionalProperties": False,
    },
    "annotations": {
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
}

LOAD_SKILL_FILE = {
    "name": "load_skill_file",
    "title": "Load a SKILL source file",
    "description": (
        "Load an existing .il or .ils file in the current Virtuoso process. Relative paths "
        "are resolved from the terminal workspace. Returns value and synchronous "
        "poport/woport/errport text in output, including warnings and errors, "
        "with output_capture=skill_ports and *_truncated flags. Ports are restored "
        "after loading; later asynchronous output is not captured. Legacy Assistant "
        "errors use data.output, and spooled results use artifact.path."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {"path": {"type": "string", "minLength": 1}},
        "required": ["path"],
        "additionalProperties": False,
    },
    "annotations": {
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
}
