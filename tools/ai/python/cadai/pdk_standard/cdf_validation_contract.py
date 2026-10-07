"""Bounded maintenance cases for the existing background CDF executor."""

from .draft_contract import ENTRY_ID, PAGE, TEXT

REF = {**TEXT, "pattern": r"^pdk-cdf:[0-9a-f]{32}$"}
VALUES = {
    "type": "object",
    "minProperties": 1,
    "maxProperties": 16,
    "additionalProperties": {"type": ["string", "number", "boolean"], "maxLength": 256},
}
CASE = {
    "type": "object",
    "properties": {
        "id": ENTRY_ID,
        "purpose": {
            "type": "string",
            "enum": ["baseline", "variation", "boundary", "off_grid", "mode_transition"],
        },
        "reason": TEXT,
        "parameters": VALUES,
        "transition": VALUES,
        "observe": {
            "type": "array",
            "items": TEXT,
            "minItems": 1,
            "maxItems": 16,
            "uniqueItems": True,
        },
    },
    "required": ["id", "purpose", "reason", "parameters", "observe"],
    "additionalProperties": False,
}
PREPARE = {
    "library": TEXT,
    "device": TEXT,
    "revision": TEXT,
    "request_id": ENTRY_ID,
    "order": {"type": "array", "items": TEXT, "minItems": 1, "maxItems": 16, "uniqueItems": True},
    "cases": {"type": "array", "items": CASE, "minItems": 1, "maxItems": 16},
}
QUERY = {"validation_ref": REF, "library": TEXT, **PAGE}
ADVANCE = {"validation_ref": REF}
FINISH = {"validation_ref": REF, "apply_execution": {"type": "boolean", "default": False}}
