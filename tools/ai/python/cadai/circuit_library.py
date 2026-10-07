"""Prepare a project library and create it only through durable operation dispatch."""

from pathlib import Path

from .circuit_create import REF, REQUEST, skill_literal
from .circuit_schema import tool
from .circuit_spec_schema import DD_NAME, CircuitSpecError, validate
from .skill_result import call_skill

LIBRARY_TOOLS = [
    tool(
        "prepare_circuit_library",
        "Read and retain target library and technology checks for this project. Supply the "
        "selected process/device library as technology_library; the backend resolves its actual "
        "technology file. Omit library to allocate a new name and path in the workspace. "
        "An existing compatible writable library is reused; never reattach an existing library. "
        "Does not create a library or edit cds.lib. Follow next_action; creation uses the "
        "returned prepare_ref via execute_circuit_operation(create_circuit_library). "
        "library_ready=true means continue to project preflight; no library creation is needed. "
        "This preparation request_id has no get_circuit_operation execution record.",
        {"task_ref": REF, "request_id": REQUEST, "library": DD_NAME,
         "technology_library": DD_NAME},
        ("task_ref", "request_id", "technology_library"),
    ),
    tool(
        "create_circuit_library",
        "Create the prepared NEW workspace library and attach the selected technology, or "
        "retain the already-compatible library. Only through execute_circuit_operation. "
        "Recheck project, library resolution, technology and cds.lib before writes; retain "
        "partial outcomes without replay. Preflight circuit views after library setup.",
        {"prepare_ref": REF, "request_id": REQUEST},
        ("prepare_ref", "request_id"), True,
    ),
]
LIBRARY_NAMES = frozenset(t["name"] for t in LIBRARY_TOOLS)


def call_library(name, args, client, workspace):
    validate(args, next(t["inputSchema"] for t in LIBRARY_TOOLS if t["name"] == name))
    if name == "prepare_circuit_library":
        if workspace is None or not Path(workspace).is_dir():
            raise CircuitSpecError("Captured project workspace unavailable")
        function = "aiLibraryPrepare"
        values = [args["task_ref"], args["request_id"], args.get("library"),
                  args["technology_library"], str(Path(workspace).resolve())]
    else:
        function, values = "aiLibraryCreate", [args["prepare_ref"], args["request_id"]]
    code = function + "(" + " ".join(skill_literal(v) for v in values) + ")"
    return call_skill(client, code, native=True)
