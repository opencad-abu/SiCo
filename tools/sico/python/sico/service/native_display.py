"""Readable, escaped native evidence prepared on the workbench data thread."""

from __future__ import annotations

from html import escape

from ..storage.native_media import raster_data, web_url
from ..transport.framing import strict_json
from .display import data_html

DISPLAY_CHARS = 64000
OUTPUT_CHARS = 64000


def unpack(value, reader):
    if value.get("truncated") and isinstance(value.get("artifact"), dict):
        artifact = value["artifact"]
        return strict_json(reader.artifact_text(artifact["path"], artifact["sha256"]))
    return value


def output_text(item, value, reader):
    """已捕获输出：返回 (最近的输出文本, 是否截断)，供详情页下方输出区使用。"""
    final = item.get("aggregatedOutput")
    if isinstance(final, str):
        return final[-OUTPUT_CHARS:], len(final) > OUTPUT_CHARS
    if "text" in item or not value.get("output_chunks"):
        return "", False
    # 回合回执封存早于迟到的命令输出，事件流里的分块才是完整内容（与进程详情同策略）。
    chunks = list(value["output_chunks"])
    pieces, size, read = [], 0, 0
    for artifact in reversed(chunks):
        chunk = strict_json(reader.artifact_text(artifact["path"], artifact["sha256"]))
        text = chunk["text"]
        pieces.append(text)
        size += len(text)
        read += 1
        if size >= OUTPUT_CHARS:
            break
    text = "".join(reversed(pieces))
    return text[-OUTPUT_CHARS:], size > OUTPUT_CHARS or read < len(chunks)


def native_parts(value, reader, *, inputs=None):
    """拆分原生证据：html 进文档区，输出单独进下方文本区（见详情页计划文档 §4.4）。"""
    remaining = DISPLAY_CHARS
    limited = False

    def block(text):
        nonlocal remaining, limited
        text = str(text or "")
        shown = text[:remaining]
        limited = limited or len(text) > remaining
        remaining -= len(shown)
        return "<pre style='white-space: pre-wrap'>" + escape(shown) + "</pre>"

    item = value.get("item")
    if not isinstance(item, dict):
        item = value
    conditions = unpack(inputs or {}, reader)
    item = {**conditions, **item}
    parts = []
    if item.get("type") == "webSearch":
        parts.append("<h3>网页检索</h3>" + block(item.get("query")))
        action = item.get("action") or {}
        parts.append(data_html({"操作": {"search": "搜索", "openPage": "打开网页",
                                       "findInPage": "页内查找"}.get(action.get("type"), "其他")}))
        if action.get("pattern"):
            parts.append(block(action["pattern"]))
        results = item.get("results") or []
        if not isinstance(results, list):
            raise ValueError("Invalid web result list")
        rows = [action, *results[:100]]
        limited = limited or len(results) > 100
        for row in rows:
            if not isinstance(row, dict):
                parts.append(block(row))
                if not remaining:
                    limited = True
                    break
                continue
            url = web_url(row.get("url"))
            if url:
                label = str(row.get("title") or url)[:2000]
                parts.append("<p><a href='" + escape(url, quote=True) + "'>"
                             + escape(label) + "</a></p>")
            if row.get("snippet") or row.get("text"):
                parts.append(block(row.get("snippet") or row.get("text")))
            if not remaining:
                limited = True
                break
    if item.get("type") == "imageGeneration":
        image_status = {"completed": "已完成", "failed": "失败", "inProgress": "进行中",
                        "in_progress": "进行中"}.get(item.get("status"), item.get("status"))
        parts.append("<h3>图像生成</h3>" + data_html({"状态": image_status,
                     "保存位置": item.get("savedPath"),
                     "透明背景": item.get("transparentBackground")}))
        if item.get("failure"):
            parts.append(data_html({"失败原因": item["failure"]}))
        if item.get("revisedPrompt"):
            parts.append(block(item["revisedPrompt"]))
        result = item.get("result")
        if raster_data(result):
            parts.append("<p>图像内容已归档。</p>")
        elif web_url(result):
            parts.append("<p><a href='" + escape(result, quote=True) + "'>图像来源</a></p>")
        else:
            parts.append("<p>暂无可显示的图像内容。</p>")
    if item.get("type") == "subAgentActivity":
        parts.append(data_html({"子任务": item.get("agentPath"),
                               "子线程": item.get("agentThreadId"),
                               "状态": {"started": "已启动", "interacted": "有新交互",
                                        "interrupted": "已中断", "completed": "已完成"}
                               .get(item.get("kind"), "未知")}))
    if item.get("type") == "contextCompaction":
        parts.append("<p>" + ("上下文正在压缩。" if value.get("running") else
                              "上下文压缩已完成。" if value.get("completion_observed")
                              else "上下文压缩结果待核对。") + "</p>")
    if "command" in item:
        parts.append("<h3>命令</h3>" + block(item["command"]))
        parts.append(data_html({"工作目录": item.get("cwd"), "退出码": item.get("exitCode"),
                                "耗时（毫秒）": item.get("durationMs")}))
    if "changes" in item:
        limited = limited or len(item["changes"]) > 100
        for change in item["changes"][:100]:
            parts.append(data_html({"文件": change.get("path"), "改动": change.get("kind")}))
            parts.append(block(change.get("diff")))
            if not remaining:
                limited = True
                break
    if "diff" in value:
        parts.append("<h3>文件差异</h3>" + block(value["diff"]))
    if "plan" in value:
        parts.append(block(value.get("explanation")))
        limited = limited or len(value["plan"]) > 100
        for step in value["plan"][:100]:
            status = {"pending": "待开始", "inProgress": "进行中",
                      "completed": "已完成"}[step["status"]]
            parts.append(block(status + " · " + step["step"]))
            if not remaining:
                limited = True
                break
    if "text" in item:
        parts.append(block(item["text"]))
    captured, captured_limited = output_text(item, value, reader)
    notes = []
    if value.get("completion_observed") is False and not value.get("running"):
        notes.append("<p>未收到操作完成回执，结果待核对。</p>")
    if limited or captured_limited:
        notes.append("<p>当前显示部分内容；会话归档保留已收到的完整数据。</p>")
    return {"html": "".join(parts) + "".join(notes), "body": "".join(parts),
            "notes": "".join(notes), "output": captured,
            "output_truncated": bool(captured_limited)}


def native_html(value, reader, *, inputs=None):
    """兼容接口：输出仍拼回正文（工具结果面板等没有独立输出区）。"""
    parts = native_parts(value, reader, inputs=inputs)
    if not parts["output"]:
        return parts["html"]
    section = ("<h3>输出</h3><pre style='white-space: pre-wrap'>"
               + escape(parts["output"]) + "</pre>")
    return parts["body"] + section + parts["notes"]
