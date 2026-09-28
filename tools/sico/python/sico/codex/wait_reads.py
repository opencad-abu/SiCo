"""Fail-closed admission of independent reads while a live decision is pending.

A pending decision pauses only the work it resolves: reads whose declared
dependencies cover that subject. An existing-design decision resolves the write
path to that target, so its evidence reads stay available while it is open.
"""

from ..core.contracts import ToolResult
from ..core.read_dependencies import DEPENDENCIES


class WaitingReads:
    def __init__(self, loop):
        self.loop = loop

    @staticmethod
    def blocked(reason):
        return ToolResult(
            "waiting_user", "用户问题尚未答复；此调用未执行。独立且已授权的资料读取可以继续。",
            {"reason": reason, "automatic_retry": False},
        )

    def check(self, name, arguments, context):
        audit = getattr(self.loop, "audit", None)
        if audit is None or not audit.pending:
            return None
        tool = self.loop.tools.resolve(name)
        # The adapter's declared effect and dependencies are the contract; the
        # execution domain says where a tool runs, not what it changes.
        if (tool is None or tool.effect != "read" or tool.input_dependencies is None
                or tool.annotations.get("readOnlyHint") is not True
                or tool.annotations.get("destructiveHint") is not False):
            return self.blocked("unproven_read_effect")
        try:
            # Validate before calling a dependency resolver, including unknown keys.
            tool.validate(arguments)
            dependencies = tool.input_dependencies(arguments, context)
            if (not isinstance(dependencies, frozenset)
                    or not dependencies <= DEPENDENCIES):
                return self.blocked("unproven_dependencies")
            audit.index.sync()
            for key, pending in audit.pending.items():
                row = audit.index.audits[key]
                if (row["task_id"] != self.loop.state.task["id"]
                        or row["context"] != context.record()
                        or row["status"] not in {"pending", "answer_received"}):
                    return self.blocked("stale_question")
                if pending["rpc_id"] is None:
                    if (pending.get("source") != "host"
                            or not self.loop.runtime
                            or pending.get("host_rpc") is not self.loop.runtime.rpc
                            or pending.get("host_connection_id") != self.loop.connection_id
                            or pending.get("host_thread_id") != self.loop.thread_id):
                        return self.blocked("stale_question_source")
                elif pending.get("source") == "elicitation":
                    audit.elicitations.current(key, {
                        "binding": row["binding"], "context": row["context"],
                        "task_id": row["task_id"],
                    })
                elif pending.get("source") == "native":
                    audit.native_current(key)
                    if not pending.get("child_thread_id") and (
                            pending["binding"].get("threadId") != self.loop.thread_id
                            or pending["binding"].get("turnId") != self.loop.steering.turn_id):
                        return self.blocked("stale_question_source")
                else:
                    return self.blocked("unknown_question_source")
                audit.index.verify_evidence(row["evidence"])
                # Use host provenance, never guess a dependency from question text,
                # model-supplied IDs, candidate names or a readOnlyHint annotation.
                # An existing-design decision gates writing to that target
                # (prepare/execute), never reading evidence about it.
                missing = ({"pdk", "model", "parameters"} if row.get("pdk_selection_ref")
                           else frozenset() if row.get("circuit_target_ref")
                           else DEPENDENCIES)
                if dependencies & (missing | {"decision"}):
                    return self.blocked("depends_on_pending_answer")
        except (ValueError, OSError, RuntimeError, KeyError, TypeError):
            return self.blocked("unproven_question_or_arguments")
        return None

    def before_start(self, call, context, task, stop, cancelled, *, internal=False):
        # Runs again after the registry lock/queue and argument validation. The
        # first admission is not a lasting permit and is never recovered/replayed.
        with self.loop.lock:
            if (stop.is_set() or cancelled() or not self.loop.active or self.loop.stale
                    or self.loop.state.task is not task or task["status"] != "executing"
                    or self.loop.state.context.record() != context.record()):
                return ToolResult("cancelled_before_start", "任务或来源已变化，工具未执行")
            return None if internal else self.check(call.name, call.inputs, context)
