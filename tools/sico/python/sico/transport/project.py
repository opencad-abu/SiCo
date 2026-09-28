"""Finite project read messages: data tokens only, never SKILL expressions."""

import re

from cadai.circuit_spec_schema import DD_NAME, array, enum, obj, validate
from cadai.project_schema import OPERATIONS

TEXT = {"type": "string", "minLength": 1, "maxLength": 128, "pattern": r"^[A-Za-z0-9_-]+$"}
SCHEMAS = {
    "project_session": obj({"candidate": {**TEXT, "maxLength": 32, "pattern": r"^[a-f0-9]{32}$"}}),
    "project_observe": obj(
        {
            "targets": array(array(DD_NAME, 3, 3), 32, 1),
            "libraries": array(DD_NAME, 64, 1),
            "presentation": enum("foreground", "background"),
        }
    ),
    "project_receipt": obj(
        {
            "session_id": TEXT,
            "key": {**TEXT, "pattern": r"^[A-Za-z0-9:_-]+$"},
            "input_digest": {**TEXT, "maxLength": 64, "pattern": r"^[a-f0-9]{64}$"},
            "operation": enum(*OPERATIONS),
        }
    ),
}
PROJECT_READ_METHODS = frozenset(SCHEMAS)
FUNCTIONS = {
    "aiProjectSession": ("project_session", ("candidate",)),
    "aiProjectObserve": ("project_observe", ("targets", "libraries", "presentation")),
    "aiProjectReceipt": ("project_receipt", ("session_id", "key", "input_digest", "operation")),
}


def validate_arguments(method, arguments):
    validate(arguments, SCHEMAS[method])
    if method == "project_receipt":
        prefix = OPERATIONS[arguments["operation"]]
        key = arguments["key"]
        if not key.startswith(prefix) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,80}", key[len(prefix) :]
        ):
            raise ValueError("Project receipt key differs from operation")


def wire_arguments(method, arguments):
    validate_arguments(method, arguments)
    if method == "project_session":
        return [arguments["candidate"]]
    if method == "project_observe":
        return [
            arguments["presentation"],
            ";".join(",".join(target) for target in arguments["targets"]),
            ",".join(arguments["libraries"]),
        ]
    return [arguments[k] for k in ("session_id", "key", "input_digest", "operation")]
