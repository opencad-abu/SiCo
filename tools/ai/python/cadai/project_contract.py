"""Validate project evidence before interpreting it; this is not an OA integrity proof."""

from .circuit_create import REQUEST
from .circuit_spec_schema import CircuitSpecError, validate
from .project_schema import OPERATIONS

RECEIPT_REFS = {
    "apply_cdf_update": ("update_ref", "cdf-update:"),
    "create_circuit_library": ("library_ref", "library:"),
    "create_circuit_from_plan": ("circuit_ref", "created:"),
    "create_template_symbol": ("symbol_ref", "template-symbol:"),
    "draw_symbol": ("symbol_ref", "template-symbol:"),
    "create_circuit_config": ("config_ref", "config:"),
    "create_simulation_setup": ("setup_ref", "simulation:"),
    "run_simulation_setup": ("run_ref", "simulation-run:"),
}
RUN_STATES = frozenset(
    {
        "preflight",
        "starting",
        "running",
        "stop_requested",
        "stopped",
        "completed",
        "failed",
        "start_unknown",
        "context_lost",
    }
)


def require(condition, message):
    if not condition:
        raise CircuitSpecError("invalid project evidence: " + message)


def session(value):
    require(isinstance(value, dict), "session must be an object")
    require(value.get("ok") is True, "session not confirmed")
    require(
        isinstance(value.get("session_id"), str) and 0 < len(value["session_id"]) <= 128,
        "session_id missing",
    )
    require(type(value.get("pid")) is int and value["pid"] > 0, "session pid missing")
    return value


def target(row):
    require(isinstance(row, list) and len(row) == 7, "target observation must have seven fields")
    require(
        isinstance(row[0], list)
        and len(row[0]) == 3
        and all(isinstance(v, str) and v for v in row[0]),
        "target identity missing",
    )
    require(all(type(row[i]) is bool for i in (1, 3, 4, 5, 6)), "target flags must be booleans")
    require(row[2] is None or isinstance(row[2], str), "target view type must be text or null")
    return row


def observation(value):
    require(isinstance(value, dict), "observation must be an object")
    require(type(value.get("gui_available")) is bool, "GUI availability missing")
    # The SKILL serializer represents an empty list as null, but missing keys are invalid.
    require("missing_apis" in value, "API observation missing")
    missing = value["missing_apis"]
    require(
        missing is None
        or (isinstance(missing, list) and all(isinstance(api, str) and api for api in missing)),
        "invalid API list",
    )
    require(isinstance(value.get("targets"), list), "target observations missing")
    for row in value["targets"]:
        target(row)
    require(isinstance(value.get("libraries"), list), "library observations missing")
    names = set()
    for row in value["libraries"]:
        require(isinstance(row, list) and len(row) == 2, "invalid library row")
        require(
            isinstance(row[0], str) and row[0] and row[0] not in names,
            "library identity missing or duplicated",
        )
        require(
            row[1] is None or (isinstance(row[1], str) and row[1].startswith("/")),
            "library path must be absolute or null",
        )
        names.add(row[0])


def journal_record(value, key):
    require(isinstance(value, dict), "journal must be an object")
    require(
        value.get("schema") == "cad.circuit.operation.v1" and value.get("key") == key,
        "journal identity differs",
    )
    operation = value.get("operation")
    require(isinstance(operation, str) and operation in OPERATIONS, "journal operation missing")
    validate(value.get("request_id"), REQUEST)
    require(OPERATIONS[operation] + value["request_id"] == key, "journal request identity differs")
    fingerprint = value.get("input_digest")
    require(
        isinstance(fingerprint, str)
        and len(fingerprint) == 64
        and all(c in "0123456789abcdef" for c in fingerprint),
        "journal input digest missing",
    )
    session(value.get("session"))
    require(
        isinstance(value.get("state"), str)
        and value["state"] in {"intent_recorded", "dispatching", "response_recorded"},
        "journal state unknown",
    )
    require(
        isinstance(value.get("created_at"), str) and value["created_at"],
        "journal timestamp missing",
    )
    for field in ("input_summary", "observation"):
        require(
            field not in value or isinstance(value[field], dict), "journal " + field + " invalid"
        )
    if value["state"] == "response_recorded":
        require(
            type(value.get("response_ok")) is bool and isinstance(value.get("response"), dict),
            "journal response missing",
        )
    return value


def receipt(value, record):
    require(isinstance(value, dict), "receipt must be an object")
    require(type(value.get("ok")) is bool, "receipt ok flag missing")
    field, prefix = RECEIPT_REFS[record["operation"]]
    require(value.get(field) == prefix + record["request_id"], "receipt reference differs")
    if record["operation"] == "run_simulation_setup":
        require(
            isinstance(value.get("state"), str) and value["state"] in RUN_STATES,
            "run state missing or unsupported",
        )
        expected = record.get("input_summary", {}).get("setup_ref")
        require(expected and value.get("setup_ref") == expected, "run setup differs")
        if value["state"] in {"running", "stop_requested", "stopped", "completed"}:
            require(
                isinstance(value.get("history"), str) and value["history"], "exact history missing"
            )
    else:
        require(type(value.get("saved")) is bool, "receipt saved flag missing")
    if record["operation"] == "create_circuit_library":
        for key in ("library", "library_path", "technology_library", "technology_path"):
            require(isinstance(value.get(key), str) and value[key], "library receipt " + key)
        require(type(value.get("created")) is bool, "library creation flag missing")
    return value
