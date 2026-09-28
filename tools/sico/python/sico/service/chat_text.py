"""Concise activity, receipt and resource text from event payloads."""

from __future__ import annotations


def activity_text(payload):
    """Turn bounded model progress into user-facing text without raw internals."""
    label = str(payload.get("label") or "正在处理任务").strip()
    tool = payload.get("tool")
    if isinstance(tool, str) and tool:
        label += "：" + tool
    turn = payload.get("turn")
    if isinstance(turn, int) and turn > 0:
        label += f"（第 {turn} 轮）"
    return label


def tool_result_text(payload):
    """Expose only a concise tool receipt; detailed data stays in the evidence view."""
    name = payload.get("name")
    result = payload.get("result")
    result = result if isinstance(result, dict) else {}
    summary = result.get("summary")
    status = result.get("status")
    if isinstance(summary, str) and summary.strip():
        detail = summary.strip()
    else:
        detail = {
            "ok": "工具已完成",
            "needs_reconcile": "工具需要核对当前设计上下文",
            "preflight_failed": "工具前置检查未通过",
            "tool_error": "工具执行失败",
            "invalid_arguments": "工具参数无效",
        }.get(str(status), "已收到工具结果")
    return (str(name).strip() + "：" if isinstance(name, str) and name.strip() else "") + detail


def resource_notice(payload):
    """Describe unavailable external resources without changing display state."""
    problems = list(payload.get("issues", []))
    problems += [r["id"] + "：" + r["message"] for r in payload.get("rows", [])
                 if r["status"] in {"unavailable", "dependency_missing", "empty"}]
    return "外部资源：\n" + "\n".join(problems) if problems else ""
