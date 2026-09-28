"""User-facing reconciliation guidance from the authoritative dispatched call list."""

from __future__ import annotations

import json


def operation_notice(operation, message):
    """Explain what must be reconciled instead of repeating a raw bridge code."""
    text = str(message or "")
    registration_lost = "not registered to this desktop" in text
    lines = ["任务中断，待核对"]
    if registration_lost:
        lines.append("当前会话注册已失效，未确认的调用不会自动重发。")
    if operation and operation.get("native"):
        native = operation["native"]
        return "\n".join([
            "原生操作结果待核对", operation["name"],
            "Codex 操作：" + native["item_id"],
            "调用：" + str(operation.get("id", "")) + "  " + str(operation.get("at", "")),
            "参数：" + json.dumps(operation.get("input", {}), ensure_ascii=False),
            str(message or ""),
            "请核对原命令、工作目录和文件现状；操作不会自动重发。",
            *(["当前会话注册已失效，请重新打开 Silicon Copilot 后核对结果。"]
              if registration_lost else []),
        ])
    missing_record = bool(operation and operation.get("name") == "get_circuit_operation"
                          and operation.get("code") == "operation_not_found")
    receipt = operation.get("receipt", {}) if operation else {}
    confirmed_failure = receipt.get("ok") is False
    if operation and operation.get("name"):
        detail = "· " + str(operation["name"])
        if operation.get("request_id"):
            detail += "（请求 " + str(operation["request_id"]) + "）"

        lines.append(detail + ("未找到执行记录" if missing_record else
                              "已返回失败回执" if confirmed_failure else "的结果没有确认"))
    if operation:
        lines.append("调用：" + str(operation.get("id", ""))
                     + "  " + str(operation.get("at", "")))
        target = operation.get("target")
        if isinstance(target, (list, tuple)):
            lines.append("目标：" + "/".join(str(v) for v in target))
        if operation.get("input"):
            lines.append("参数：" + json.dumps(operation["input"], ensure_ascii=False))
        if operation.get("decision"):
            lines.append("已核对：" + {"completed": "已完成", "not_executed": "未执行"}[operation["decision"]])
    if confirmed_failure:
        target = receipt.get("target")
        if not operation.get("target") and isinstance(target, list):
            lines.append("目标：" + "/".join(str(v) for v in target))
        facts = []
        for key, label in (("stage", "阶段"), ("instance", "实例")):
            if receipt.get(key):
                facts.append(label + "：" + str(receipt[key]))
        if receipt.get("saved") is False:
            facts.append("未保存")
        if facts:
            lines.append("；".join(facts))
        if receipt.get("error"):
            lines.append(str(receipt["error"]))
    if message:
        lines.append(str(message))
    if registration_lost:
        lines.append("请重新打开 Silicon Copilot 后先核对原请求与目标现状；"
                     "不要直接重跑未确认的操作。")
        return "\n".join(lines)
    if missing_record:
        lines.append("这次中断未创建审阅事项。请核对执行请求 ID 与目标现状；"
                     "准备请求不产生执行记录。"
                     "可查询原请求的现状，不能据此重放写操作。")
    elif confirmed_failure:
        lines.append("下一步：先只读检查目标与失败参数，保留未保存现场；"
                     "原请求不会重放。处理已有内容前需确认。")
    else:
        lines.append("下一步：先查询该请求的现状，确认后再继续；根据证据选择已完成或未执行，结束中断任务（不重放）。")
    return "\n".join(lines)


def reconcile_notice(operations, message):
    """Render the same durable call list for live chat and history paging."""
    if not operations:
        return ("任务中断，待核对\n本轮没有待核对的已派发操作，可直接结束中断任务继续。\n"
                + str(message or "") + "\n下一步：结束中断任务并继续（不重放）。"
                + ("\n当前会话注册已失效，请重新打开 Silicon Copilot。"
                   if "not registered to this desktop" in str(message) else ""))
    unknown = sum(row.get("outcome") == "unconfirmed" for row in operations)
    lines = [f"待核对 · {len(operations)} 项操作（{unknown} 项没有回执） · 会话已暂停",
             "原调用不会自动重发；仍不确定时保持暂停。"]
    lines.extend(str(index) + ". " + operation_notice(row, "")
                 for index, row in enumerate(operations, 1))
    if message:
        lines.append(str(message))
    if "not registered to this desktop" in str(message):
        lines.append("当前会话注册已失效，请重新打开 Silicon Copilot 后核对原请求与目标现状；不要直接重跑未确认的操作。")
    return "\n\n".join(lines)
