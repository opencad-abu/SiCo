"""Bounded project checks and explicitly journalled circuit operations."""

from .circuit_create import REQUEST
from .circuit_schema import PRESENTATION, string_schema, tool
from .circuit_spec_schema import DD_NAME, TARGET, array, enum, obj

PATH = string_schema(2048, pattern=r"^/.*")
OPERATIONS = {
    "apply_cdf_update": "cdf-update:",
    "create_circuit_library": "library-create:",
    "create_circuit_from_plan": "create-execute:",
    "edit_circuit": "edit-execute:",
    "create_template_symbol": "template-symbol:",
    "draw_symbol": "template-symbol:",
    "create_circuit_config": "config:",
    "create_simulation_setup": "simulation-create:",
    "run_simulation_setup": "simulation-run:",
}
# Operations that execute through a differently named tool. ``edit_circuit``
# (the reviewed in-place edit) runs through ``execute_circuit_edit``; every
# other operation is dispatched by its own, identically named tool.
OPERATION_TOOLS = {
    "edit_circuit": "execute_circuit_edit",
}


def operation_tool(operation: str) -> str:
    """The registered tool that carries an operation's complete input schema."""

    return OPERATION_TOOLS.get(operation, operation)


PROJECT_TOOLS = [
    tool(
        "preflight_circuit_project",
        "Read current Virtuoso library/target/API state and explicit "
        "project directories/files/executable. No OA write, model execution or license checkout. "
        "Filesystem checks describe the MCP host, not remote scheduler workers. Passing checks "
        "does not qualify writes, models, licenses or simulation. Supply project expectations.",
        {
            "presentation": PRESENTATION,
            "targets": array(
                obj(
                    {
                        **TARGET["properties"],
                        "intent": enum("create", "read", "edit"),
                        "view_type": enum("schematic", "schematicSymbol", "maestro", "config"),
                    }
                ),
                32,
                1,
            ),
            "libraries": array(obj({"library": DD_NAME, "expected_path": PATH}), 32),
            "directories": array(
                obj(
                    {
                        "path": PATH,
                        "purpose": enum("project", "run", "temp"),
                        "min_free_bytes": {"type": "integer", "minimum": 0, "maximum": 10**15},
                        "min_free_inodes": {"type": "integer", "minimum": 0, "maximum": 10**12},
                    }
                ),
                16,
                1,
            ),
            "model_files": array(PATH, 64),
            "simulator_executable": PATH,
        },
        ("presentation", "targets", "libraries", "directories", "model_files"),
    ),
    tool(
        "execute_circuit_operation",
        "Journal circuit creation or an inspected schematic update, Symbol/Config/Maestro "
        "creation or exact-history run before dispatch. operation names an existing tool; "
        "arguments must satisfy that tool's complete schema, including request_id. Use after "
        "project preflight and confirmed task mode. Same operation/request_id returns recovery "
        "status without redispatch, including partial failures or lost replies. Existing schematic "
        "content requires an inspection and user decision bound to preparation. Never replay unknown writes. "
        "Existing direct tools remain available without this disk journal.",
        {
            "operation": enum(*OPERATIONS),
            "arguments": {"type": "object", "maxProperties": 32, "additionalProperties": True},
        },
        ("operation", "arguments"),
        True,
    ),
    tool(
        "get_circuit_operation",
        "Read a journalled operation after disconnect/restart. "
        "refresh=true queries only the original live Virtuoso identity and retained receipt "
        "and updates the local journal observation (no OA write); refresh=false reads the "
        "local record offline without writes. Missing receipt, partial write or session "
        "mismatch never "
        "permits automatic replay. Use the request_id from execute_circuit_operation, not from "
        "a preparation tool. operation_not_found means no local execution record: correct the "
        "request identity and inspect the target, without replaying writes. "
        "Historical success does not prove current design integrity.",
        {
            "operation": enum(*OPERATIONS),
            "request_id": REQUEST,
            "refresh": {"type": "boolean"},
        },
        ("operation", "request_id"),
    ),
]
PROJECT_NAMES = frozenset(row["name"] for row in PROJECT_TOOLS)
