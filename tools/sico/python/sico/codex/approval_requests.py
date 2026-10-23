"""One-shot native approvals on the durable, task-bound audit surface."""

import json
from copy import deepcopy

from ..core.audit_questions import bounded, questions
from ..core.review_classes import BLOCKING
from .input_records import native_record
from .access_policy import default_access
from .input_delivery import send_controls

METHODS = {"item/commandExecution/requestApproval", "item/fileChange/requestApproval",
           "item/permissions/requestApproval"}
CHOICES = {"拒绝": "decline", "仅批准本次": "accept", "批准本回合权限": "accept", "取消回合": "cancel"}


def denied(method):
    return ({"permissions": {"fileSystem": {"entries": []}, "network": {"enabled": False}},
             "scope": "turn"} if method == "item/permissions/requestApproval"
            else {"decision": "decline"})


def details(runtime, event):
    params, method = event["params"], event["method"]
    value = {k: deepcopy(v) for k, v in params.items()
             if k not in {"threadId", "turnId", "startedAtMs", "availableDecisions"}}
    if method == "item/fileChange/requestApproval":
        key = (params["threadId"], params["turnId"], params["itemId"])
        changes = runtime.approval_changes(key)
        if not changes:
            raise ValueError("File approval requires the original change details")
        value["changes"] = changes
    elif method == "item/commandExecution/requestApproval":
        network = params.get("networkApprovalContext")
        if network is not None:
            bounded(network["host"], 1000)
            bounded(network["protocol"], 32)
        else:
            bounded(params.get("command"), 16000)
    elif not isinstance(params.get("permissions"), dict):
        raise ValueError("Missing requested permissions")
    return bounded(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), 60000)


def open_request(audit, event, turn_id, *, child=None):
    with audit.runtime.lock:
        reply = _prepare(audit, event, turn_id, child=child)
    if reply:
        send_controls([reply])


def _prepare(audit, event, turn_id, *, child=None):
    scope, params, method = audit.runtime.snapshot(), event["params"], event["method"]
    audit.requests.connect()
    binding = {k: bounded(params[k], 200) for k in ("threadId", "turnId", "itemId")}
    binding.update(connection_id=audit.requests.connection_id, isBlocking=True,
                   method=method, approvalId=params.get("approvalId"))
    claim = audit.requests.check(event["id"], params["threadId"], [binding, params])
    if claim is None:
        return
    access = scope.access.get(
        "access", default_access())
    if access["approval"] == "never" or scope.origin == "startup":
        audit.requests.remember(claim)
        return audit.requests.connection, {"id": event["id"], "result": denied(method)}
    if len(audit.pending) >= 4 or sum(r["task_id"] == scope.task_id
                                    for r in audit.index.audits.values()) >= 32:
        raise ValueError("Approval request budget exceeded")
    description = details(audit.runtime, event)
    approval = {"method": method}
    if method == "item/commandExecution/requestApproval":
        # 审批卡片的命令片段：命令原文保留真实换行，界面按代码块呈现；
        # rationale 里的 JSON 转义形式只作证据，不再承担可读性。
        command = params.get("command")
        if isinstance(command, str) and command.strip() and len(command) <= 16000:
            approval["command"] = command
        for source, target, limit in (("environmentId", "environment", 200),
                                      ("cwd", "cwd", 1000)):
            value = params.get(source)
            if isinstance(value, str) and value.strip() and len(value) <= limit:
                approval[target] = value
    choices = ["拒绝", "仅批准本次", "取消回合"]
    if method == "item/permissions/requestApproval":
        choices = ["拒绝", "批准本回合权限"]
    available = params.get("availableDecisions")
    if available is not None:
        choices = [c for c in choices if CHOICES[c] in available]
    if not choices:
        raise ValueError("No supported one-shot approval decision")
    incoming = questions([{"id": "codex_approval", "header": "Codex 执行审批",
        "question": "请核对本事项的完整操作依据后选择；批准仅适用于当前请求。",
        "options": [{"label": c, "description": {
            "拒绝": "拒绝此操作，模型可继续寻找其他方案。",
            "仅批准本次": "允许列出的操作或权限；不创建永久放行规则。",
            "批准本回合权限": "将列出的权限授予当前回合；回合结束后失效。",
            "取消回合": "拒绝此操作并中断当前回合。"}[c]} for c in choices]}])
    current = audit.active_work()
    row = native_record(incoming, scope.task_id, scope.context,
                        current, audit.index.data[current["source"]]["digest"], child,
                        params["turnId"], audit.index.audits.values(), len(audit.pending),
                        audit.runtime.child_scope if child else None,
                        review_class_value=BLOCKING)
    row.update(rationale="\n\n" + "\n".join("    " + line for line in description.splitlines()),
               native_approval=approval)
    audit.emit("prepared", row)
    pending = {"rpc_id": event["id"], "binding": binding,
        "blocking": True, "review_class": BLOCKING, "source": "approval", "questions": incoming,
        "approval": deepcopy(params), "child_thread_id": child.thread_id if child else "",
        "child_turn_id": params["turnId"] if child else ""}
    audit.open_input(row["id"], pending, {"id": row["id"], **binding}, claim=claim)
    if row["id"] in audit.pending:
        audit.runtime.activity("waiting_user", "等待答复：" + row["title"])


def validate_answer(row, answers):
    if "native_approval" not in row:
        return
    answer = answers["codex_approval"]
    if answer["choice"] not in CHOICES or answer["text"]:
        raise ValueError("执行审批必须明确选择一个选项，不接受自由文本授权")


def response(row, pending):
    if pending.get("source") != "approval":
        return {"answers": {q: {"answers": [v for v in (a["choice"], a["text"]) if v]}
                            for q, a in row["reply"]["answers"].items()}}
    validate_answer(row, row["reply"]["answers"])
    decision = CHOICES[row["reply"]["answers"]["codex_approval"]["choice"]]
    method = pending["binding"]["method"]
    if method == "item/permissions/requestApproval":
        return ({"permissions": deepcopy(pending["approval"]["permissions"]), "scope": "turn"}
                if decision == "accept" else denied(method))
    return {"decision": decision}
