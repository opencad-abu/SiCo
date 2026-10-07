"""MCP schemas for verification operations."""


RUN_AIVW_RECIPE = {
    "name": "run_aivw_recipe",
    "title": "Run AI Verification Workbench recipe",
    "description": (
        "Run one recipe gate and its dependency closure from the controller-delegated "
        "workspace. This writes immutable AIVW control/payload artifacts and may start "
        "EDA tools, so review the request before calling it. Publication remains "
        "unavailable through "
        "the generic recipe command."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "reference": {
                "type": "string",
                "pattern": "^[a-z][a-z0-9_.-]{0,255}$",
            },
            "through": {
                "type": "string",
                "pattern": "^[a-z][a-z0-9_.-]{0,255}$",
            },
            "profile": {
                "type": "string",
                "pattern": "^[a-z][a-z0-9_.-]{0,255}$",
                "default": "amsverify",
            },
            "timeout": {"type": "number", "minimum": 1, "maximum": 3600},
            "candidate_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
                "description": "Optional controller-owned candidate to bind to deterministic feedback.",
            },
        },
        "required": ["reference", "through"],
        "additionalProperties": False,
    },
    "annotations": {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
}

SUBMIT_CANDIDATE = {
    "name": "submit_candidate",
    "title": "Submit bounded candidate",
    "description": (
        "Record one bounded RNM or Verilog-A candidate in the controller-owned private "
        "ledger. This stages bytes for deterministic gates; it does not publish to OA and "
        "does not accept an agent verdict."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "source": {"type": "string", "minLength": 1, "maxLength": 262144},
            "module": {"type": "string", "pattern": "^[A-Za-z_][A-Za-z0-9_.$+-]{0,127}$"},
            "language": {"type": "string", "enum": ["systemverilog", "veriloga"]},
            "source_generation": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "template_lock": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "parent_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "context_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "assumptions": {
                "type": "array",
                "maxItems": 32,
                "items": {"type": "string", "minLength": 1, "maxLength": 512},
            },
        },
        "required": ["source", "module", "language", "source_generation"],
        "additionalProperties": False,
    },
    "annotations": {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
        "approvalRequired": False,
    },
}

GET_CANDIDATE = {
    "name": "get_candidate",
    "title": "Read candidate metadata",
    "description": "Read one staged candidate and its controller-generated deterministic gate feedback.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "candidate_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "include_source": {"type": "boolean", "default": False},
        },
        "required": ["candidate_sha256"],
        "additionalProperties": False,
    },
    "annotations": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
}
