"""Shared project checks/recovery through the bound, read-only Copilot transport."""

from copy import deepcopy
from pathlib import Path

from cadai.circuit_spec_schema import CircuitSpecError, validate
from cadai.project_schema import PROJECT_TOOLS
from cadai.project_tools import call_project

from ..core.contracts import ToolResult
from ..core.tools import ReadTool
from ..transport.methods import QueryUnavailable
from ..transport.project import FUNCTIONS

RECONCILE_STATES = frozenset(
    {
        "partial_or_failed",
        "outcome_unknown",
        "session_mismatch",
        "context_lost",
        "target_changed",
        "start_unknown",
        "starting",
    }
)


def preflight_summary(data):
    failures = []
    for row in data.get("checks", []):
        if row.get("status") != "fail":
            continue
        actual = row.get("actual")
        actual = actual if isinstance(actual, dict) else {}
        target = "/".join(str(v) for v in actual.get("target", []))
        if row.get("check") == "target_create" and actual.get("exists"):
            failures.append(f"{target} 已存在；先检查当前内容，空白视图可直接沿用，已有设计需确认继续或替换")
        else:
            failures.append(str(row.get("check", "未知检查项")) + ("：" + target if target else ""))
    return "项目预检未通过：" + "；".join(failures[:6]) if failures else "项目预检未通过，请检查预检结果。"


def operation_summary(data):
    receipt = data.get("receipt")
    if not isinstance(receipt, dict) or receipt.get("ok") is not False:
        return ""
    target = receipt.get("target")
    target = "/".join(str(v) for v in target) if isinstance(target, list) else ""
    detail = "操作已返回失败回执"
    if target:
        detail += "：" + target
    if receipt.get("stage"):
        detail += "；阶段 " + str(receipt["stage"])
    if receipt.get("instance"):
        detail += "；实例 " + str(receipt["instance"])
    if receipt.get("saved") is False:
        detail += "；未保存"
    if receipt.get("error"):
        detail += "。" + str(receipt["error"])
    if (
        receipt.get("stage") == "check"
        and "schCheck" in str(receipt.get("error") or "")
    ):
        # A check warning is a single local defect (usually a labelled stub that
        # does not touch its pin), not a reason to rebuild the whole route.
        detail += (
            " 请只修复被点名的悬空点（对齐端口/器件引脚，或删除多余短线）后重跑；"
            "不要重建整个电路或新建单元，同一 request_id 重发不会重复写入。"
        )
    return detail


def operation_status(data, *, reading=False):
    receipt = data.get("receipt")
    state = data.get("state")
    if (data.get("ok") is True and isinstance(receipt, dict) and receipt.get("ok") is False
            and state in {"partial_or_failed", "target_changed", "failed"}):
        return "ok" if reading else "operation_failed"
    failure = data.get("dispatch_error", {})
    if (state == "dispatch_rejected" and failure.get("operation_dispatched") is False
            and failure.get("category") in {"preflight", "validation", "binding"}):
        return data.get("code", "preflight_failed")
    if state in RECONCILE_STATES or data.get("ok") is not True:
        return "needs_reconcile"
    return "ok"


class ProjectReadClient:
    def __init__(self, broker, context):
        self.broker, self.context = broker, context

    def call_project_native(self, function, values):
        if function not in FUNCTIONS:
            raise ValueError("Copilot project adapter only supports native project reads")
        method, keys = FUNCTIONS[function]
        if len(keys) != len(values):
            raise ValueError("Invalid project read arguments")
        reply = self.broker.read(self.context, method, dict(zip(keys, values)))
        data = reply.get("project")
        if not isinstance(data, dict):
            raise ValueError("Missing project read evidence")
        return data


def register_project(registry, broker, workspace, client_for=None):
    def handler(name):
        def read(arguments, context):
            # The local journal/filesystem belong to the session's project, not
            # a model-provided path. Offline recovery performs no broker calls.
            cwd = context.snapshot.get("cwd")
            if not isinstance(cwd, str) or not Path(cwd).is_absolute():
                return ToolResult("unavailable", "Missing captured project directory")
            if Path(cwd).resolve() != Path(workspace).resolve():
                return ToolResult("needs_reconcile", "Project workspace differs from task source")
            try:
                client = (
                    client_for(context)[0] if client_for else ProjectReadClient(broker, context)
                )
                if name == "preflight_circuit_project" and client_for:
                    client.requirements = None
                data = call_project(name, arguments, client, workspace, {})
            except (QueryUnavailable, CircuitSpecError, OSError) as exc:
                return ToolResult(
                    "needs_reconcile" if name == "get_circuit_operation" else "preflight_failed",
                    "Project evidence unavailable; reconcile before continuing",
                    {
                        "ok": False,
                        "error_type": type(exc).__name__,
                        "message": str(exc)[:4096],
                        "automatic_resume_allowed": False,
                    },
                )
            if name == "get_circuit_operation" and data.get("code") != "operation_not_found":
                status = operation_status(data, reading=True)
                return ToolResult(
                    status, operation_summary(data) or data.get("dispatch_diagnostic", ""), data
                )
            if name == "get_circuit_operation" and data.get("code") == "operation_not_found":
                return ToolResult(
                    "operation_not_found",
                    "当前工程未找到该执行记录；请核对 execute_circuit_operation 的请求 ID，"
                    "准备请求不产生执行记录。可继续读取目标，不能据此重放写操作。",
                    data,
                )
            if name == "preflight_circuit_project" and not data.get("checks_passed"):
                return ToolResult("preflight_failed", preflight_summary(data), data)
            if name == "preflight_circuit_project" and client_for:
                client.requirements = deepcopy(arguments)
            return ToolResult("ok" if data.get("ok") is True else "needs_reconcile", data=data)

        return read

    for definition in PROJECT_TOOLS:
        if definition["name"] == "execute_circuit_operation":
            continue  # Registered by the write adapter with the shared execution handler.
        registry.register(
            ReadTool(
                definition["name"],
                definition["description"],
                definition["inputSchema"],
                lambda args, schema=definition["inputSchema"]: validate(args, schema),
                handler(definition["name"]),
                annotations=definition["annotations"],
            )
        )
