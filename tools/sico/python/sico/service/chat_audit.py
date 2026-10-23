"""Audit question text and approval snippets projected from explicit display fields."""

from __future__ import annotations


def approval_command(row):
    """命令审批的展示片段 ``(meta, command)``；其它审批返回 ``None``。

    ``command`` 保留命令原文的真实换行：卡片按代码块呈现，不再依赖
    ``rationale`` JSON 里的转义形式；``meta`` 是执行环境与工作目录。
    """

    approval = row.get("native_approval")
    if not isinstance(approval, dict):
        return None
    command = approval.get("command")
    if not isinstance(command, str) or not command.strip():
        return None
    meta = " · ".join(str(approval[key]) for key in ("environment", "cwd")
                      if approval.get(key))
    return meta, command


def fence_block(code):
    """把整段代码包成安全围栏的 markdown 代码块；内容自带围栏时自动加长。"""

    text = str(code or "").rstrip("\n")
    fence = "```"
    while any(line.startswith(fence) for line in text.splitlines()):
        fence += "`"
    return fence + "\n" + text + "\n" + fence


def audit_status_label(status, *, has_reply=False, elicitation=False,
                       review_class="blocking", automatic=False):
    """One durable-state label for the audit list and conversation card."""
    label = {
        "pending": "待答复",
        "answer_received": "答复已提交，正在核验",
        "answered": "已答复，任务继续",
        "invalid": ("答复未交付，问题已失效" if has_reply
                    else "问题已失效，尚未答复"),
        "dispatching": "正在发送答复",
        "unconfirmed": "答复交付未确认",
        "withdrawn": ("答复未交付，问题已失效" if has_reply
                      else "问题已失效，尚未答复"),
    }.get(status, "")
    if elicitation and status == "answered":
        label = "答复已发送，服务结果另行核对"
    if automatic and status == "answered":
        label = "补充事项已按建议继续"
    elif review_class == "supplementary":
        label = "补充事项（模型可自行决定）" + ("：" + label if label else "")
    return label


def audit_text(title, status, questions, *, recommendation, reason, elicitation,
               recommended_label, review_class="blocking", automatic=False,
               has_reply=False):
    """Format an audit without receiving its message or mutating its state."""
    label = audit_status_label(
        status, has_reply=has_reply, elicitation=elicitation,
        review_class=review_class, automatic=automatic,
    )
    parts = [label + "：" + title]
    if recommendation:
        parts.append("建议：" + recommendation)
    for q in questions:
        parts.append(q["header"] + "：" + q["question"])
        parts.extend(
            recommended_label(o["label"]) + "：" + o["description"] for o in q["options"]
        )
    if reason:
        parts.append(reason)
    return "\n\n".join(parts)
