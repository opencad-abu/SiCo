"""Bounded source-fact inputs; usage decisions are deliberately absent."""

from .draft_contract import ENTRY_ID, LOCATOR, PAGE, TEXT

REFERENCE = {**TEXT, "pattern": r"^pdk-facts:[0-9a-f]{32}$"}
SOURCE = {
    "type": "object",
    "properties": {"file_ref": TEXT, "document_revision": TEXT, "locator": LOCATOR},
    "required": ["file_ref", "document_revision", "locator"],
    "additionalProperties": False,
}
FACT = {
    "type": "object",
    "properties": {
        "id": ENTRY_ID,
        "device": TEXT,
        "parameter": TEXT,
        "field": {
            "type": "string",
            "enum": ["type", "unit", "meaning", "default", "domain", "grid"],
        },
        "disposition": {"type": "string", "enum": ["established", "context", "unresolved"]},
        "basis": {
            "type": "string",
            "enum": [
                "definition",
                "input_constraint",
                "pcell_limit",
                "model_characterization",
                "recommendation",
                "incomplete",
            ],
        },
        "source": SOURCE,
        "reason": {"type": "string", "minLength": 1, "maxLength": 1024},
        "value": True,
        "input_unit": TEXT,
        "complete": {"type": "boolean"},
        "unbounded_sides": {
            "type": "array",
            "items": {"type": "string", "enum": ["min", "max"]},
            "uniqueItems": True,
            "maxItems": 2,
        },
        "grid_status": {"type": "string", "enum": ["none", "specified", "unknown"]},
        "expected": True,
    },
    "required": ["id", "device", "parameter", "field", "disposition", "basis", "source", "reason"],
    "additionalProperties": False,
}
COLLECT = {
    "library": TEXT,
    "revision": TEXT,
    "request_id": ENTRY_ID,
    "facts": {"type": "array", "minItems": 1, "maxItems": 32, "items": FACT},
}
QUERY = {"report_ref": REFERENCE, "library": TEXT, **PAGE}
