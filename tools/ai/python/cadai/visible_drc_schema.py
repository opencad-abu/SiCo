"""MCP schemas for composed visible-window DRC jobs."""

from .visible_drc import CANCEL_NAME, GET_NAME, LIST_NAME, NAME, START_NAME

VISIBLE_DRC_NAMES = frozenset({NAME, START_NAME, GET_NAME, CANCEL_NAME, LIST_NAME})

_PATH_PATTERN = r"^/(?:[^\u0000-\u001F\u007F]*[^\s\u0000-\u001F\u007F])?$"
_JOB_PATTERN = r"^visible-[a-f0-9]{32}$"

_START_PROPERTIES = {
    "window_ref": {
        "type": "string",
        "pattern": "^wctx_[A-Za-z0-9_-]+_[0-9]{1,10}$",
    },
    "config_path": {"type": "string", "pattern": _PATH_PATTERN},
    "work_root": {"type": "string", "pattern": _PATH_PATTERN},
    "calibre_root": {"type": "string", "pattern": _PATH_PATTERN},
    "timeout_seconds": {
        "type": "integer",
        "minimum": 1,
        "maximum": 1800,
        "default": 300,
    },
    "region_user_units": {
        "type": "array",
        "items": {"type": "number"},
        "minItems": 4,
        "maxItems": 4,
    },
}
_START_REQUIRED = ["window_ref", "config_path", "work_root", "calibre_root"]
_CHECK_PROPERTIES = {
    **_START_PROPERTIES,
    "auto_previous": {
        "type": "boolean",
        "default": True,
    },
    "previous_job_ref": {
        "type": "string",
        "pattern": _JOB_PATTERN,
    },
}

CHECK = {
    "name": NAME,
    "title": "Check visible layout DRC",
    "description": (
        "Export the visible region, run one bounded Calibre candidate DRC, and "
        "return deltas. Fragment only; never full-chip signoff."
    ),
    "inputSchema": {
        "type": "object",
        "properties": _CHECK_PROPERTIES,
        "required": list(_START_REQUIRED),
        "additionalProperties": False,
    },
    "annotations": {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
}

START = {
    "name": START_NAME,
    "title": "Start visible layout DRC",
    "description": (
        "Export the visible layout region and submit one asynchronous DataHub "
        "candidate DRC job. An optional region_user_units exports an explicit "
        "user-unit bbox. Returns a visible-* job_ref for status polling."
    ),
    "inputSchema": {
        "type": "object",
        "properties": dict(_START_PROPERTIES),
        "required": list(_START_REQUIRED),
        "additionalProperties": False,
    },
    "annotations": {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
}

GET = {
    "name": GET_NAME,
    "title": "Get visible layout DRC",
    "description": "Read status, result and events for one job.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "job_ref": {"type": "string", "pattern": _JOB_PATTERN},
            "after": {"type": "integer", "minimum": 0, "maximum": 2147483647},
        },
        "required": ["job_ref"],
        "additionalProperties": False,
    },
    "annotations": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
}

CANCEL = {
    "name": CANCEL_NAME,
    "title": "Cancel visible layout DRC",
    "description": (
        "Stop an active visible-window candidate DRC job and return its "
        "explicit cancelled or terminal state."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {"job_ref": {"type": "string", "pattern": _JOB_PATTERN}},
        "required": ["job_ref"],
        "additionalProperties": False,
    },
    "annotations": {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
}

LIST = {
    "name": LIST_NAME,
    "description": "List jobs.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "offset": {"type": "integer", "minimum": 0, "maximum": 4096},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        "required": [],
    },
    "annotations": {
        "readOnlyHint": True,
        "idempotentHint": True,
    },
}

VISIBLE_DRC_TOOLS = (CHECK, START, GET, CANCEL, LIST)

__all__ = [
    "CANCEL",
    "CHECK",
    "GET",
    "LIST",
    "START",
    "VISIBLE_DRC_NAMES",
    "VISIBLE_DRC_TOOLS",
]
