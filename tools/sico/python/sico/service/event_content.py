"""Prepare bounded transport slices of one complete, readable event document."""

from .event_decode import readable_payload
from .event_fields import fields_html, text_block
from .tool_display import TITLES

EVENT_TITLES = {
    "task.started": "用户请求", "model.delta": "回复片段", "model.completed": "完整回复",
    "tool.started": "工具调用", "tool.finished": "工具结果",
    "codex.plan.updated": "执行计划", "codex.diff.updated": "文件改动",
    "codex.native.updated": "操作记录", "codex.history.item": "历史操作记录",
}
REFERENCE_FIELDS = frozenset({"id", "native", "origin", "context", "contract", "archive"})


def _body(payload, content_format):
    text = payload.get("text")
    if isinstance(text, str):
        yield text_block(text) if content_format == "html" else text
    fields = {key: value for key, value in payload.items()
              if key not in REFERENCE_FIELDS and not (key == "text" and isinstance(text, str))}
    yield from fields_html(fields)
    references = {key: value for key, value in payload.items() if key in REFERENCE_FIELDS}
    if references:
        yield from fields_html(references, ("来源信息",))


def detail_page(event, offset, limit, *, reader=None):
    """Format before slicing; the UI never parses partial JSON or shows partial HTML."""
    payload = readable_payload(event, reader)
    title = EVENT_TITLES.get(event["kind"], "完整记录")
    if event["kind"] in {"tool.started", "tool.finished"} and payload.get("name"):
        title += " · " + TITLES.get(payload["name"], payload["name"])
    text = payload.get("text")
    markdown = event["kind"] in {"model.delta", "model.completed"} and isinstance(text, str)
    content_format = "markdown" if markdown else (
        "plain" if set(payload) == {"text"} and isinstance(text, str) else "html")
    pieces, total = [], 0
    for part in _body(payload, content_format):
        end = total + len(part)
        if end > offset and total < offset + limit:
            pieces.append(part[max(0, offset - total):offset + limit - total])
        total = end
    return {"title": title[:256], "text": "".join(pieces), "offset": min(offset, total),
            "total": total, "next": min(total, offset + limit), "format": content_format,
            "body_chars": len(text) if markdown else 0}
