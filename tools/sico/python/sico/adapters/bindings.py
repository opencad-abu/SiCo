"""Session-scoped host reconciliation; no model-supplied ownership or evidence."""

from ..core.contracts import ToolResult
from ..core.tools import Tool, no_arguments
from ..transport.router_journal import RouterJournal


def register_bindings(registry, broker, session_id):
    if not callable(getattr(broker, "reconcile_binding_operation", None)):
        return

    def inspect(arguments, context):
        snapshot = broker.binding_snapshot(session_id)
        return ToolResult(
            data={
                key: snapshot[key]
                for key in (
                    "binding_id",
                    "holds",
                    "operations",
                    "invalidated",
                )
            }
        )

    def validate(arguments):
        if not isinstance(arguments, dict) or set(arguments) != {"request_id"}:
            raise ValueError("An exact original router request_id is required")
        RouterJournal.request_id(arguments["request_id"])

    def reconcile(arguments, context):
        snapshot = broker.binding_snapshot(session_id)
        result = broker.reconcile_binding_operation(
            session_id, snapshot["binding_id"], arguments["request_id"]
        )
        return ToolResult(
            "ok" if result["hold_cleared"] else "binding_" + result["state"],
            "Original operation reconciled: " + result["state"],
            result,
        )

    registry.register(
        Tool(
            "get_binding_operations",
            "List this logical session's unsettled native operations "
            "and reconciliation decisions, including original router request IDs.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            no_arguments,
            inspect,
            execution_domain="host",
        )
    )
    registry.register(
        Tool(
            "reconcile_binding_operation",
            "Query evidence for one original native router request. "
            "Only verified terminal evidence clears its execution hold. Keeps resource ownership; "
            "never replays a write, resumes a task, or transfers a window.",
            {
                "type": "object",
                "properties": {"request_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"}},
                "required": ["request_id"],
                "additionalProperties": False,
            },
            validate,
            reconcile,
            execution_domain="host",
            annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
        )
    )
