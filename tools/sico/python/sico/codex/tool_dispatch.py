"""One admitted tool's physical dispatch and bounded receipt encoding."""


from ..core.contracts import NeedsReconcile, ToolResult
from ..core.tools import tool_message
from ..transport.broker import skill_call_context


def execute_call(call, context, task, stop, cancelled, *, internal, journal,
                 active, before_start, execute):
    name = call.name
    try:
        def progress(record):
            with journal.lock:
                if journal.state.task is not task or task["status"] != "executing":
                    return
                journal.emit("router.status", record)
                if active() and not stop.is_set():
                    if record["state"] == "queued":
                        journal.activity("queued", "等待 SKILL 队列：" + record["request_id"], tool=name)
                    else:
                        journal.activity("tool_call", "执行工具", tool=name)

        from cadai.process_monitor import process_scope

        with process_scope(str(journal.journal.directory)), skill_call_context(session_id=journal.journal.session_id,
                task_id=task["id"], tool_call_id=call.id,
                cancelled=lambda: stop.is_set() or cancelled(), progress=progress,
                before_start=lambda: before_start(
                    call, context, task, stop, cancelled, internal=internal)):
            result = execute(call, context)
    except NeedsReconcile as exc:
        result = ToolResult("needs_reconcile", str(exc), exc.data)
    return result


def encode_receipt(call, result, limit, artifact):
    message = tool_message(call, result, limit, artifact)
    return message, {"content": [{"type": "text", "text": message["content"]}],
                     "isError": result.status != "ok"}
