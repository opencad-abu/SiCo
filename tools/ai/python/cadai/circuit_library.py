"""Prepare a project library and create it only through durable operation dispatch."""

import hashlib
from pathlib import Path

from .circuit_create import REF, REQUEST, skill_literal
from .circuit_schema import tool
from .circuit_spec_schema import DD_NAME, CircuitSpecError, validate
from .skill_result import call_skill

LIBRARY_TOOLS = [
    tool(
        "prepare_library",
        "Read-only library/technology preflight. Optional task_ref; when absent a background "
        "library task is created. Optional technology_library supports bound or unbound "
        "libraries. Reuse compatible writable libraries; a mismatch returns reattach_required, "
        "set reattach_technology=true only after the user's explicit choice. For simple "
        "create/reuse prefer execute_circuit_operation(create_circuit_library).",
        {"task_ref": REF, "request_id": REQUEST, "library": DD_NAME,
         "technology_library": DD_NAME,
         "reattach_technology": {"type": "boolean", "default": False}},
        ("request_id",),
        help_text=(
            "Follow next_action. This preparation request_id has no get_circuit_operation "
            "execution record. When the target library exists with another technology, ask "
            "the user: allocate a new compatible library, change this library's technology "
            "binding (then repeat with reattach_technology=true), or stop; never reattach "
            "without that explicit choice."),
    ),
    tool(
        "create_circuit_library",
        "Create the prepared NEW workspace library and attach the selected technology, "
        "re-attach the user-approved existing library to it, or retain the already-compatible "
        "library. Only through execute_circuit_operation. "
        "Recheck project, library resolution, technology and cds.lib before writes; retain "
        "partial outcomes without replay. Preflight circuit views after library setup. "
        "Can run directly with library and optional technology; prepare_library is only needed "
        "for explicit stepwise review.",
        {"prepare_ref": REF, "request_id": REQUEST, "library": DD_NAME,
         "technology_library": DD_NAME,
         "reattach_technology": {"type": "boolean", "default": False}},
        ("request_id",), True,
    ),
]
LIBRARY_NAMES = frozenset(t["name"] for t in LIBRARY_TOOLS)


def call_library(name, args, client, workspace):
    if name == "prepare_circuit_library":
        name = "prepare_library"
    validate(args, next(t["inputSchema"] for t in LIBRARY_TOOLS if t["name"] == name))
    if name == "prepare_library":
        if workspace is None or not Path(workspace).is_dir():
            raise CircuitSpecError("Captured project workspace unavailable")
        task_ref = args.get("task_ref")
        if not isinstance(task_ref, str) or not task_ref.startswith("task:"):
            task_ref = ensure_library_task(client, args["request_id"])
        function = "aiLibraryPrepare"
        values = [task_ref, args["request_id"], args.get("library"),
                 args.get("technology_library"), str(Path(workspace).resolve()),
                 args.get("reattach_technology", False)]
        result = call_skill(client, function + "(" + " ".join(
            skill_literal(v) for v in values) + ")", native=True)
        if isinstance(result, tuple) and len(result) == 2:
            ok, data = result
            if ok and isinstance(data, dict):
                data["task_ref"] = task_ref
        return result
    else:
        if not args.get("prepare_ref"):
            raise CircuitSpecError("prepare_ref is required after library preparation")
        function, values = "aiLibraryCreate", [args["prepare_ref"], args["request_id"]]
    code = function + "(" + " ".join(skill_literal(v) for v in values) + ")"
    return call_skill(client, code, native=True)


class LibraryPreparationError(CircuitSpecError):
    def __init__(self, message, data=None):
        super().__init__(message)
        self.data = data or {}


def ensure_library_task(client, request_id):
    """Create a deterministic background task for a library-only operation."""
    task_id = "lib-" + hashlib.sha256(request_id.encode()).hexdigest()[:32]
    ok, data = call_skill(
        client,
        "aiCircuitBeginTask(" + skill_literal(task_id) + " \"background\" t)",
        native=True,
    )
    if not ok or data.get("ok") is not True or not data.get("task_ref"):
        raise LibraryPreparationError("library task could not be recorded", data)
    return data["task_ref"]


def prepare_library_operation(client, parameters, workspace):
    """Prepare a one-step create request; the caller then dispatches create."""
    library = parameters.get("library")
    if not library:
        raise CircuitSpecError("library is required when prepare_ref is omitted")
    task_ref = ensure_library_task(client, parameters["request_id"])
    prepare_args = {
        "task_ref": task_ref,
        "request_id": parameters["request_id"],
        "library": library,
        "reattach_technology": parameters.get("reattach_technology", False),
    }
    if parameters.get("technology_library"):
        prepare_args["technology_library"] = parameters["technology_library"]
    ok, prepared = call_library(
        "prepare_library",
        prepare_args,
        client,
        workspace,
    )
    if not ok or prepared.get("ok") is not True:
        raise LibraryPreparationError("library preparation failed", prepared)
    if prepared.get("reattach_required"):
        raise LibraryPreparationError(
            "existing library requires an explicit reattach decision", prepared)
    return task_ref, prepared
