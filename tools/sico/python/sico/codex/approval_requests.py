"""One-shot native approvals on the durable, task-bound audit surface."""

import json
from copy import deepcopy

from ..service.audit_questions import bounded, questions
from ..service.audit_records import native_record
from .access_policy import default_access

METHODS = {"item/commandExecution/requestApproval", "item/fileChange/requestApproval",
           "item/permissions/requestApproval"}
CHOICES = {"拒绝": "decline", "仅批准本次": "accept", "批准本回合权限": "accept", "取消回合": "cancel"}


def denied(method):
    return ({"permissions": {"fileSystem": {"entries": []}, "network": {"enabled": False}},
             "scope": "turn"} if method == "item/permissions/requestApproval"
            else {"decision": "decline"})


def details(loop, event):
    params, method = event["params"], event["method"]
    value = {k: deepcopy(v) for k, v in params.items()
             if k not in {"threadId", "turnId", "startedAtMs", "availableDecisions"}}
    if method == "item/fileChange/requestApproval":
        key = (params["threadId"], params["turnId"], params["itemId"])
        item = loop.native.pending.get(key, {}).get("input", {})
        if item.get("truncated"):
            asset = item["artifact"]
            item = json.loads(loop.journal.artifact_text(asset["path"], asset["sha256"]))
        if not item.get("changes"):
            raise ValueError("File approval requires the original change details")
        value["changes"] = item["changes"]
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
    loop, params, method = audit.loop, event["params"], event["method"]
    with loop.lock:
        audit.requests.connect()
        binding = {k: bounded(params[k], 200) for k in ("threadId", "turnId", "itemId")}
        binding.update(connection_id=audit.requests.connection_id, isBlocking=True,
                       method=method, approvalId=params.get("approvalId"))
        claim = audit.requests.check(event["id"], params["threadId"], [binding, params])
        if claim is None:
            return
        access = (loop.state.task.get("turn_options") or loop.turn_options.selected).get(
            "access", default_access())
        if access["approval"] == "never" or loop.state.task.get("origin") == "startup":
            loop.runtime.rpc.send({"id": event["id"], "result": denied(method)})
            audit.requests.remember(claim)
            return
        if len(audit.pending) >= 4 or sum(r["task_id"] == loop.state.task["id"]
                                        for r in audit.index.audits.values()) >= 32:
            raise ValueError("Approval request budget exceeded")
        description = details(loop, event)
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
        current = audit.workbench.active()
        row = native_record(incoming, loop.state.task["id"], loop.state.context.record(),
                            current, audit.index.data[current["source"]]["digest"], child,
                            params["turnId"], audit.index.audits.values(), len(audit.pending),
                            loop.child_inputs.scope if child else None)
        row.update(rationale="\n\n" + "\n".join("    " + line for line in description.splitlines()),
                   native_approval={"method": method})
        audit.emit("prepared", row)
        audit.pending[row["id"]] = {"rpc_id": event["id"], "binding": binding,
            "blocking": True, "source": "approval", "questions": incoming,
            "approval": deepcopy(params), "child_thread_id": child.thread_id if child else "",
            "child_turn_id": params["turnId"] if child else ""}
        loop.state.task["waiting_audits"] = list(audit.pending)
        audit.emit("opened", {"id": row["id"], **binding})
        audit.requests.remember(claim)
        loop._activity("waiting_user", "等待答复：" + row["title"])


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
