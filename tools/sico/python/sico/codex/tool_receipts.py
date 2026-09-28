"""Apply an admitted tool receipt to the current task and host questions."""

import json


def reconciliation_reason(call, result):
    code = result.data.get("code") if isinstance(result.data, dict) else None
    reason = result.summary or f"{call.name}: {result.status}"
    if not result.summary and isinstance(code, str):
        reason += f" ({code})"
    return reason


def current_receipt(call, result, context, task, message, *, journal, tool_started_at, audit,
                    mark_stale):
    if journal.state.task is not task or task["status"] != "executing":
        journal.emit("session.tool_receipt", {
            "task_id": task["id"], "source": context.record(),
            **call.record(), "result": json.loads(message["content"]),
        })
        return False
    if result.status == "needs_reconcile":
        mark_stale(reconciliation_reason(call, result))
    journal.state.task["pending_calls"].remove(call.record())
    tool_started_at.pop(call.id, None)
    finished = journal.emit(
        "tool.finished",
        {
            "id": call.id,
            "name": call.name,
            "result": json.loads(message["content"]),
        },
    )
    data = result.data
    if (audit is not None and isinstance(data, dict) and data.get("update_ref")
            and data.get("confirmation_required") and data.get("selection_question")):
        audit.require_pdk_update(data, f"d_{finished['sequence']}_tool", context)
    if (audit is not None and isinstance(data, dict)
            and data.get("schema_version") == "cad.pdk.preparation.v1"
            and data.get("selection_required") and data.get("selection_question")):
        audit.require_pdk_choice(data, f"d_{finished['sequence']}_tool", context)
    if (audit is not None and isinstance(data, dict)
            and data.get("target_ref") and data.get("decision_required")
            and data.get("selection_question")):
        audit.require_target_choice(data, f"d_{finished['sequence']}_tool", context)
    return True
