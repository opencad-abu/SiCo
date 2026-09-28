"""Audit question text projected from explicit display fields."""

from __future__ import annotations


def audit_text(title, status, questions, *, recommendation, reason, elicitation,
               recommended_label):
    """Format an audit without receiving its message or mutating its state."""
    label = {"pending": "待答复", "answer_received": "答复已提交，正在核验",
              "answered": "已答复，任务继续", "invalid": "答复未交付",
              "dispatching": "正在发送答复", "unconfirmed": "答复交付未确认",
              "withdrawn": "任务已结束，此问题不再等待答复"}.get(status, "")
    if elicitation and status == "answered":
        label = "答复已发送，服务结果另行核对"
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
