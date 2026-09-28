"""Passive document/target presentation shared by the desktop."""

from __future__ import annotations

from datetime import datetime
from html import escape


def event_time(value):
    """Local ``MM-DD HH:MM`` for a journal or row timestamp; empty when unknown."""
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return ""
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone()
    return stamp.strftime("%m-%d %H:%M")


def token_amount(value):
    """Compact model usage: K/M/B with one decimal, exact below a thousand.

    A value that would round to 1000.0 in its unit moves up a unit instead
    (999950 is 1.0M, not 1000.0K).
    """
    if not isinstance(value, int) or value < 0:
        return "0"
    units = ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K"))
    for index, (limit, suffix) in enumerate(units):
        if value < limit:
            continue
        if index > 0 and value / limit >= 999.95:
            limit, suffix = units[index - 1]
        return f"{value / limit:.1f}{suffix}"
    return str(value)


RECOMMENDED_SUFFIX = " (Recommended)"
RECOMMENDED_LABEL = "（推荐值）"
# The model also writes the marker in Chinese, or with different case/width.
RECOMMENDED_MARKERS = (RECOMMENDED_SUFFIX, RECOMMENDED_LABEL, "（推荐）")


def plain_recommendation(value):
    """Label without its trailing recommendation marker, for display and matching."""
    text = str(value).strip()
    folded = text.casefold()
    for marker in RECOMMENDED_MARKERS:
        if folded.endswith(marker.casefold()):
            return text[: len(text) - len(marker)].strip()
    return text


def is_recommended(value):
    """True when the host or the model marked this label as the preferred choice."""
    return plain_recommendation(value) != str(value).strip()


def recommended_label(value):
    """Show the host's recommendation marker in the UI language."""
    text = str(value).strip()
    if not is_recommended(text):
        return text
    return plain_recommendation(text) + RECOMMENDED_LABEL


FIELD_LABELS = {
    "artifact": "数据归档", "cell": "单元", "cellview": "设计视图",
    "context": "设计上下文", "contract": "数据类型", "data": "数据",
    "description": "说明", "diagnostic": "诊断", "error": "错误",
    "history": "History", "input": "输入", "kind": "记录类型",
    "lib": "库", "library": "库", "message": "说明", "model": "模型",
    "name": "名称", "operation": "操作", "origin": "发起方式",
    "path": "路径", "payload": "记录内容", "reason": "原因", "result": "结果",
    "schema": "数据格式", "sequence": "记录序号", "session_id": "会话编号",
    "size": "大小（字节）", "snapshot": "来源快照", "source": "来源",
    "status": "状态", "summary": "摘要", "target": "目标", "task_id": "任务编号",
    "test": "Test", "text": "内容", "timestamp": "时间", "unit": "单位",
    "valid": "是否有效", "value": "数值", "view": "视图",
    "corner": "Corner", "point": "点", "output": "输出", "metric": "指标",
    "qualification": "规格判定", "spec_qualified": "满足所列规格",
    "coverage_complete": "覆盖完整", "parameters": "参数条件", "counts": "统计",
    "pass": "通过", "fail": "不满足", "missing": "缺失", "unit_source": "单位来源",
    "lower": "下限", "upper": "上限", "margin": "裕量", "qualified": "是否符合",
}


def data_html(data):
    """Render bounded field/value details without serializing structured data as text."""
    rows, limited = [], False

    def visit(value, path, depth):
        nonlocal limited
        if len(rows) >= 160 or depth > 8:
            limited = True
            return
        if isinstance(value, (dict, list)) and value:
            entries = value.items() if isinstance(value, dict) else enumerate(value, 1)
            for key, child in entries:
                if len(rows) >= 160:
                    limited = True
                    break
                # A truncated tool envelope contains a serialized JSON prefix, not a value.
                if isinstance(value, dict) and value.get("truncated") and key == "preview":
                    continue
                label = FIELD_LABELS.get(str(key), str(key))[:160]
                visit(child, [*path, label], depth + 1)
            return
        if value is None:
            rendered = "未提供"
        elif isinstance(value, bool):
            rendered = "是" if value else "否"
        elif isinstance(value, (dict, list)):
            rendered = "无记录"
        else:
            rendered = str(value)
        if len(rendered) > 2000:
            rendered = rendered[:2000] + "…"
            limited = True
        rows.append((" / ".join(path) or "数据", rendered))

    visit(data, [], 0)
    body = "".join(
        "<tr><td width='38%'>" + escape(label) + "</td><td>"
        + escape(value).replace("\n", "<br>") + "</td></tr>"
        for label, value in rows
    )
    result = "<table width='100%' cellspacing='0' cellpadding='5'>" + body + "</table>"
    if limited:
        result += "<p>当前显示部分字段；完整数据保留在会话记录中。</p>"
    return result


def target_label(snapshot):
    cellview = snapshot.get("cellview")
    design = (
        " / ".join(str(cellview.get(key, "?")) for key in ("lib", "cell", "view"))
        if isinstance(cellview, dict)
        else "CIW（未绑定设计视图）"
    )
    details = ["当前目标：" + design]
    for key, label in (("ade_session", "ADE"), ("test", "Test"), ("history", "History")):
        if snapshot.get(key):
            details.append(label + "：" + str(snapshot[key]))
    if snapshot.get("valid") is False:
        details.append("目标已失效，请重新核对")
    return "  ·  ".join(details)
