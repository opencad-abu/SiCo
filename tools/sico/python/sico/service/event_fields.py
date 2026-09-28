"""Escaped HTML for full event fields, with aligned values and separate output blocks."""

from html import escape

from .display import FIELD_LABELS
from .tool_display import TITLES

LABELS = {
    **FIELD_LABELS,
    "id": "记录编号", "name": "名称", "input": "输入参数", "result": "执行结果",
    "value": "记录内容", "item": "操作内容", "items": "记录列表",
    "native": "原生记录标识", "origin": "来源记录", "context": "设计上下文",
    "arguments": "调用参数", "content": "正文", "command": "执行命令",
    "cwd": "工作目录", "exitCode": "退出码", "durationMs": "耗时（毫秒）",
    "aggregatedOutput": "执行输出", "stdout": "标准输出", "stderr": "错误输出",
    "changes": "文件改动", "diff": "改动内容", "type": "类型",
    "thread_id": "线程编号", "turn_id": "回合编号", "item_id": "操作编号",
    "item_type": "操作类型", "completion_observed": "已收到完成回执",
    "output_chunks": "输出归档", "untrusted": "外部返回内容",
    "truncated": "内容已归档或截断", "preview": "内容预览", "sha256": "内容校验值",
    "questions": "待确认的问题", "question": "问题", "options": "可选答案",
    "answers": "用户答复", "choice": "选择", "label": "选项", "header": "标题",
    "title": "标题", "recommendation": "建议", "plan": "执行计划", "step": "步骤",
    "explanation": "说明", "inputs": "输入资料", "turn_options": "回合设置",
    "tool_calls": "工具调用",
}
STATUSES = {
    "ok": "成功", "success": "成功", "completed": "已完成", "recorded": "已记录",
    "failed": "失败", "error": "错误", "tool_error": "工具执行失败",
    "needs_reconcile": "结果待核对", "unconfirmed": "结果未确认",
    "pending": "待处理", "running": "执行中", "executing": "执行中",
    "inProgress": "进行中", "in_progress": "进行中", "cancelled": "已取消",
}

LABELS.update({"archive": "完整内容归档", "input_tokens": "输入 Token",
               "output_tokens": "输出 Token", "complete": "内容完整", "value": "数值",
               "enabled": "启用", "rows": "记录列表"})
OUTPUT_FIELDS = frozenset({"command", "code", "script", "diff", "output", "aggregatedOutput",
                           "stdout", "stderr", "diagnostic", "error"})


def scalar(value, key):
    if value is None:
        return "未提供"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (dict, list, tuple)):
        return "无记录"
    if isinstance(value, str):
        if key == "status" and value in STATUSES:
            return STATUSES[value] + "（" + value + "）"
        if key == "name" and value in TITLES:
            return TITLES[value] + "（" + value + "）"
    return str(value) if value != "" else "（空文本）"


def _heading(path):
    return '<h3 style="margin-top:16px; margin-bottom:6px; color:#B4543A">' + escape(
        path[-1]) + "</h3>"


def text_block(text, *, output=False):
    # Escape once for HTML, preserving angle brackets as text rather than tags.
    style = "white-space:pre-wrap; margin:6px 0;"
    if output:
        style += " font-family:monospace; background-color:#F4F4F4;"
    return '<pre style="' + style + '">' + escape(text) + "</pre>"


def _row(key, value):
    label, text = LABELS.get(key, key), scalar(value, key)
    return ('<tr><td width="27%" valign="top" bgcolor="#F4F4F4"><b>' + escape(label)
            + '</b></td><td width="73%" valign="top">' + escape(text) + '</td></tr>')


def _short(key, value):
    return (not isinstance(value, (dict, list, tuple)) or not value) and (
        key not in OUTPUT_FIELDS and len(str(value)) <= 200 and "\n" not in str(value))


def _grid(rows):
    return '<table width="100%" cellspacing="0" cellpadding="7">' + "".join(rows) + '</table>'


def fields_html(value, path=()):
    """Walk every field; unlike bounded summaries, this full view does not drop rows."""
    entries = value.items() if isinstance(value, dict) else (
        (f"第 {index} 项", child) for index, child in enumerate(value, 1))
    if path:
        yield _heading(path)
    rows = []
    for key, child in entries:
        if _short(key, child):
            rows.append(_row(key, child))
            continue
        if rows:
            yield _grid(rows)
            rows = []
        label = LABELS.get(key, key)
        if isinstance(child, (dict, list, tuple)) and child:
            yield from fields_html(child, (*path, label))
        else:
            yield _heading((*path, label))
            yield text_block(scalar(child, key), output=key in OUTPUT_FIELDS)
    if rows:
        yield _grid(rows)
