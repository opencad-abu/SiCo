"""Structured instance CDF updates using installed callbacks and durable write dispatch."""

from .cdf_tools import REF, REQUEST, TASK
from .circuit_schema import string_schema, tool
from .circuit_spec_schema import ID, VALUE, CircuitSpecError, array, mapping, validate
from .simulation_recipe import call_simulation_native

PREPARE_REF = string_schema(128, pattern=r"^cdf-update-prepare:[A-Za-z0-9_-]+$")
CDF_UPDATE_TOOLS = [
    tool(
        "prepare_cdf_update",
        "Prepare a bounded change to one inspected schematic instance from its fresh instance "
        "cdf_ref; prepare only, no write and no callbacks. parameters are literal values; "
        "callback_order lists each changed parameter once in the intended edit order. "
        "The built-in executor runs installed CDF procedures and doneProc. Rejects modified views, "
        "stale evidence and unsupported callback syntax. Reuse task presentation; execute "
        "with execute_circuit_operation after preflight for the returned target with "
        "intent=edit.",
        {"cdf_ref": REF, "task_ref": TASK, "request_id": REQUEST,
         "parameters": {**mapping(VALUE, maximum=16), "minProperties": 1},
         "callback_order": array(ID, 16, 1)},
        ("cdf_ref", "task_ref", "request_id", "parameters", "callback_order"),
        help_text=(
            "The executor uses the real effective cell CDF, formInitProc and cdfUpdateInstParam "
            "(including doneProc); no user adapter registration, callback source or script path is "
            "required. GUI editable/display conditions are ignored; only edited parameters' "
            "callbacks are invoked."),
    ),
    tool(
        "apply_cdf_update",
        "Apply a retained CDF update through execute_circuit_operation. Saves a verified backup, "
        "rechecks target and CDF procedure identities, applies parameters in the prepared order, "
        "runs installed callbacks and doneProc, restores shared CDF values and verifies saved "
        "instance readback. Failures keep backup and diagnostic evidence; installed PDK procedure "
        "effects are not a sandboxed transaction. Recover unknown outcomes with "
        "get_circuit_operation and the original request_id; never replay.",
        {"prepare_ref": PREPARE_REF, "request_id": REQUEST},
        ("prepare_ref", "request_id"), mutating=True,
    ),
]
CDF_UPDATE_NAMES = frozenset(t["name"] for t in CDF_UPDATE_TOOLS)


def call_cdf_update(name, args, client):
    validate(args, next(t["inputSchema"] for t in CDF_UPDATE_TOOLS if t["name"] == name))
    if name == "prepare_cdf_update":
        order = args["callback_order"]
        if len(order) != len(set(order)) or set(order) != set(args["parameters"]):
            raise CircuitSpecError("callback_order must contain every changed parameter exactly once")
        rows = []
        for key in order:
            value = args["parameters"][key]
            kind = "boolean" if type(value) is bool else "string" if isinstance(value, str) else (
                "integer" if type(value) is int else "number")
            rows.append([key, kind, value])
        return call_simulation_native(client, "aiCdfUpdatePrepare", [args["cdf_ref"], args["task_ref"],
                                                                      args["request_id"], rows])
    return call_simulation_native(client, "aiCdfUpdateApply", [args["prepare_ref"], args["request_id"]])
