"""Private manual-mapping workflow contract; never an effective PDK document."""

import re
from copy import deepcopy

from ..circuit_spec_schema import CircuitSpecError
from ..circuit_spec_schema import validate as check_schema
from .jsonio import fail

FORMAT = "sico.pdk.parameter-draft.v1"
TEXT = {"type": "string", "minLength": 1, "maxLength": 256}
REF = {**TEXT, "pattern": r"^pdk-draft:[0-9a-f]{32}$"}
ENTRY_ID = {**TEXT, "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"}
LOCATOR = {
    "type": "object",
    "properties": {
        "page": {"type": "integer", "minimum": 1, "maximum": 100000},
        "section": TEXT,
        "anchor": TEXT,
    },
    "additionalProperties": False,
    "anyOf": [{"required": [key]} for key in ("page", "section", "anchor")],
}
SOURCE = {
    "type": "object",
    "properties": {"file_ref": TEXT, "document_revision": TEXT, "unavailable_reason": TEXT},
    "additionalProperties": False,
    "oneOf": [
        {
            "required": ["file_ref", "document_revision"],
            "not": {"required": ["unavailable_reason"]},
        },
        {
            "required": ["unavailable_reason"],
            "not": {"anyOf": [{"required": ["file_ref"]}, {"required": ["document_revision"]}]},
        },
    ],
}
ENTRY = {
    "type": "object",
    "properties": {
        "entry_id": ENTRY_ID,
        "device": TEXT,
        "cdf": TEXT,
        "manual_term": TEXT,
        "meaning": TEXT,
        "user_term": TEXT,
        "locator": LOCATOR,
        "relation": TEXT,
        "role": {"type": "string", "enum": ["input", "selector", "derived", "device"]},
        "status": {"type": "string", "enum": ["candidate", "unresolved", "rejected"]},
        "batch_id": ENTRY_ID,
        "difference": {"type": "string", "minLength": 1, "maxLength": 1024},
    },
    "required": ["entry_id", "device", "manual_term", "role", "status", "batch_id"],
    "additionalProperties": False,
}
PAGE = {
    "page_size": {"type": "integer", "minimum": 1, "maximum": 32, "default": 20},
    "cursor": TEXT,
}
OP = {
    "type": "object",
    "properties": {
        "op": {"type": "string", "enum": ["add", "replace", "remove"]},
        "path": {"type": "string", "minLength": 1, "maxLength": 1024},
        "value": True,
    },
    "required": ["op", "path"],
    "additionalProperties": False,
}
CHANGES = {
    "type": "array",
    "minItems": 1,
    "maxItems": 10,
    "items": {
        "type": "object",
        "properties": {
            "file": TEXT,
            "ops": {"type": "array", "minItems": 1, "maxItems": 100, "items": OP},
        },
        "required": ["file", "ops"],
        "additionalProperties": False,
    },
}


def validate(value, schema):
    try:
        check_schema(value, schema)
    except (CircuitSpecError, TypeError, ValueError) as exc:
        fail(str(exc))


def entry(value):
    validate(value, ENTRY)
    if value["role"] == "device" and "cdf" in value:
        fail("Device-usage mapping cannot name a CDF parameter")
    if value["status"] == "candidate":
        if not value.get("locator") or (value["role"] != "device" and not value.get("cdf")):
            fail("Candidate requires a manual locator and actual CDF name")
        if value["role"] != "device" and not all(value.get(k) for k in ("meaning", "user_term")):
            fail("Parameter candidate requires meaning and user terminology")
        if value.get("difference"):
            fail("Unresolved differences require status=unresolved")
    elif value["status"] == "unresolved" and not value.get("difference"):
        fail("Unresolved mapping requires a difference description")
    return deepcopy(value)


def reference(value):
    if not isinstance(value, str) or not re.fullmatch(REF["pattern"], value):
        fail("Invalid PDK mapping draft reference", "pdk_update_unavailable")
    return value.split(":")[1]
