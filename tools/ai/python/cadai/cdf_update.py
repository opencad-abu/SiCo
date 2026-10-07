"""Structured instance CDF updates using installed callbacks and durable write dispatch."""

from .cdf_tools import REF, REQUEST, TASK
from .circuit_schema import string_schema, tool
from .circuit_spec_schema import ID, VALUE, CircuitSpecError, array, mapping, validate
from .simulation_recipe import call_simulation_native

PREPARE_REF = string_schema(128, pattern=r"^cdf-update-prepare:[A-Za-z0-9_-]+$")
CDF_UPDATE_TOOLS = [
    tool(
        "prepare_cdf_update",
        "Prepare a bounded change to one inspected schematic instance using its fresh instance "
        "cdf_ref. Does not write or run callbacks. parameters are literal values; callback_order "
        "must list each changed parameter exactly once in the intended edit order. The built-in "
        "executor uses installed CDF procedures, real effective cell CDF, formInitProc and "
        "cdfUpdateInstParam (including doneProc). No user adapter registration, callback source "
        "or script path is required. Rejects modified views, stale evidence, buttons and unsupported "
        "callback syntax. GUI editable/display conditions are ignored; only edited parameters' "
        "callbacks are invoked. Reuse task presentation. Execute with execute_circuit_operation after "
        "project preflight for the returned target with intent=edit.",
        {"cdf_ref": REF, "task_ref": TASK, "request_id": REQUEST,
         "parameters": {**mapping(VALUE, maximum=16), "minProperties": 1},
         "callback_order": array(ID, 16, 1)},
        ("cdf_ref", "task_ref", "request_id", "parameters", "callback_order"),
    ),
    tool(
        "apply_cdf_update",
        "Apply a retained CDF update through execute_circuit_operation. Saves a verified backup, "
        "rechecks target and CDF procedure identities, applies parameters in the prepared order, "
        "runs installed callbacks and doneProc, restores shared CDF values and verifies saved "
        "instance readback. Failures retain backup and diagnostic evidence; arbitrary effects "
        "inside installed PDK procedures are not a sandboxed transaction. Unknown outcomes must "
        "be recovered using get_circuit_operation with the original request_id, never replayed.",
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
