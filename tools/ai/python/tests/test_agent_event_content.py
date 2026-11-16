"""Readable event details retain evidence across formatting and page boundaries."""

from copy import deepcopy
from html.parser import HTMLParser

import pytest

from sico.service.event_content import detail_page


class VisibleText(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.feed(source)

    def handle_data(self, value):
        self.parts.append(value)


def visible(source):
    return "".join(VisibleText(source).parts)


def event(payload, kind="tool.finished"):
    return {"kind": kind, "payload": payload}


def test_nested_tool_result_is_readable_and_does_not_change_evidence():
    raw = event({"id": "call-1", "name": "codex_command", "result": {
        "status": "tool_error", "summary": "检查未通过", "data": {"item": {
            "command": "check --all", "cwd": "/work/design", "exitCode": 2,
            "aggregatedOutput": "第一行\n第二行\n  保留输出缩进\n",
        }, "valid": False, "missing": None, "extra_field": "保留未知字段"},
    }})
    before = deepcopy(raw)
    page = detail_page(raw, 0, 8192)
    assert page["title"] == "工具结果 · 命令执行" and page["format"] == "html"
    text = visible(page["text"])
    for expected in ("执行结果", "状态工具执行失败（tool_error）", "摘要检查未通过",
                     "执行命令check --all", "退出码2", "是否有效否", "缺失未提供",
                     "第一行\n第二行\n  保留输出缩进\n", "extra_field保留未知字段",
                     "来源信息", "记录编号call-1"):
        assert expected in text
    assert text.index("执行结果") < text.index("来源信息")
    assert '"result":' not in text and "\\n" not in text
    assert '<td width="27%"' in page["text"] and '<td width="73%"' in page["text"]
    assert raw == before


def test_text_with_additional_fields_keeps_every_field():
    raw = event({"text": "正文", "message": "补充说明", "result": {"value": 3.14159}})
    text = visible(detail_page(raw, 0, 8192)["text"])
    assert text.startswith("正文")
    assert "补充说明" in text and "3.14159" in text


@pytest.mark.parametrize("body", ["", "回复\n" * 10000, '<img src="file:///tmp/private">'])
def test_text_only_body_keeps_exact_character_offsets(body):
    raw = event({"text": body}, "model.completed")
    for offset in (0, 4096):
        page = detail_page(raw, offset, 4096)
        assert page["text"] == body[offset:offset + 4096]
        assert page["total"] == len(body)
        assert page["next"] == min(len(body), offset + 4096)


def test_many_fields_and_long_unicode_values_survive_every_page():
    payload = {"input": {"rows": [{"index": i, "value": f"结果 {i}"} for i in range(700)]},
               "result": {"output": "汉字🙂\n" * 7000}, "last_field": "最后一个字段"}
    raw = event(payload)
    complete = detail_page(raw, 0, 1000000)
    offset, parts = 0, []
    while True:
        page = detail_page(raw, offset, 4096)
        assert len(page["text"]) <= 4096
        assert page["total"] == complete["total"]
        assert page["offset"] == offset
        parts.append(page["text"])
        if page["next"] == page["total"]:
            break
        assert page["next"] > offset
        offset = page["next"]
    assert "".join(parts) == complete["text"]
    text = visible(complete["text"])
    assert "第 700 项" in text and "结果 699" in text
    assert payload["result"]["output"] in text and "最后一个字段" in text


def test_unknown_values_and_empty_containers_are_not_omitted():
    raw = event({"data": {"new_field": {"enabled": True}, "list": [], "object": {},
                           "status": "future_status", "empty": "", "number": 0}})
    text = visible(detail_page(raw, 0, 8192)["text"])
    for expected in ("new_field", "启用是", "list无记录", "object无记录",
                     "状态future_status", "empty（空文本）", "number0"):
        assert expected in text


def test_only_assistant_body_is_marked_as_markdown():
    body = "# 回复\n\n**结论**"
    for kind, expected in (("model.completed", "markdown"), ("model.delta", "markdown"),
                           ("task.started", "html"), ("tool.finished", "html")):
        raw = event({"text": body, "message": "附加字段"}, kind)
        page = detail_page(raw, 0, 4096)
        assert page["format"] == expected
        assert page["body_chars"] == (len(body) if expected == "markdown" else 0)
        assert body in page["text"] and "附加字段" in page["text"]
