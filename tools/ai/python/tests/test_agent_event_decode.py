"""Serialized tool text and archived previews become verified readable content."""

import json
from copy import deepcopy

import pytest
from agent_replay_fixtures import record_task
from agent_worker_fixtures import window as window
from test_agent_event_content import visible
from test_agent_event_publication import next_batch, stream_for

from sico.service.event_content import detail_page
from sico.service.event_decode import normalized, readable_payload


@pytest.mark.parametrize("layers", [1, 2, 3])
def test_nested_serialized_json_decodes_unicode_quotes_and_newlines(layers):
    output = '读取完成\n名称："M1"\n  参数有效\n'
    data = {"summary": "结果", "output": output, "value": 0.000000001}
    encoded = json.dumps(data, ensure_ascii=True)
    for _ in range(layers - 1):
        encoded = json.dumps(encoded)
    raw = {"kind": "tool.finished", "payload": {"result": {"data": encoded}}}
    before = deepcopy(raw)
    text = visible(detail_page(raw, 0, 20000)["text"])
    assert output in text and '名称："M1"' in text
    assert "1e-09" in text and "\\u" not in text and "\\n" not in text
    assert raw == before


def test_literal_code_paths_regex_and_invalid_json_survive():
    values = {"command": r'printf "a\nb"', "path": r'C:\new\test', "regex": r'\w+\n',
              "code": '{"literal": "\\n"}', "pattern": r'\t',
              "description": r'Type \n to insert a line break.', "partial": '{"text":"abc\\n',
              "duplicate": '{"value": 1, "value": 2}', "number": '{"value": NaN}'}
    assert normalized(values) == values


def test_fenced_json_and_actual_terminal_controls_are_rendered_as_text():
    raw = {"kind": "tool.finished", "payload": {"result": {
        "content": '```json\n{"message":"第一行\\n第二行"}\n```',
        "output": '\x1b[31m错误\x1b[0m\n<img src="file:///private">\n'
                  '\x1b]0;terminal title\x07正文',
    }}}
    page = detail_page(raw, 0, 20000)
    text = visible(page["text"])
    assert "第一行\n第二行" in text and "```" not in text
    assert "错误\n<img src=\"file:///private\">\n正文" in text
    assert "\x1b" not in text and "terminal title" not in text
    assert '<img src=' not in page["text"] and '&lt;img src=' in page["text"]


def test_serialized_assistant_body_is_rendered_as_fields():
    raw = {"kind": "model.completed", "payload": {"text": json.dumps({
        "message": "读取完成\n无错误", "data": {"value": 42},
    }, ensure_ascii=True)}}
    page = detail_page(raw, 0, 20000)
    assert page["format"] == "html"
    assert "读取完成\n无错误" in visible(page["text"])
    assert '"message":' not in visible(page["text"])


@pytest.mark.parametrize("history", [False, True])
def test_complete_archive_replaces_preview_in_live_and_history(window, history):
    controller = window.controller
    _, state = record_task(controller.journal, controller.current, "large-tool", terminal=False)
    full = {"status": "ok", "summary": "已完成", "data": {
        "output": "已完成一行\n" * 3000, "message": "完整归档末尾",
    }}
    encoded = json.dumps(full, ensure_ascii=False)
    archive = controller.journal.artifact(encoded.encode("utf-8"))
    raw = controller.journal.append("tool.finished", {"id": "call", "name": "read_artifact",
        "result": {"status": "ok", "summary": "已完成", "truncated": True,
                   "preview": encoded[:8000], "artifact": archive}}, state)
    stream = (window.sessions.worker.preview_session("initial", 1).result(5).stream
              if history else stream_for(window))
    batch = next_batch(window, stream)
    event = next(row for row in batch.events if row["kind"] == "tool.finished")
    before = deepcopy(raw)
    offset, chunks = 0, []
    while True:
        page = window.sessions.worker.event_detail(stream, event["detail"], offset).result(5)
        chunks.append(page["text"])
        if page["next"] == page["total"]:
            break
        offset = page["next"]
    text = visible("".join(chunks))
    assert text.count("已完成一行") == 3000
    assert "完整归档末尾" in text and "内容预览" not in text
    assert '"data":' not in text and "完整内容归档" in text
    assert raw == before
    # A checksum failure cannot silently replace the full message with a prefix.
    (controller.journal.root / archive["path"]).write_text("damaged")
    with pytest.raises(ValueError, match="checksum"):
        window.sessions.worker.event_detail(stream, event["detail"], 0).result(5)


def test_arbitrary_nested_artifact_is_not_followed():
    class Reader:
        def artifact_text(self, *_args):
            raise AssertionError("Only known protocol offloads may be resolved")

    raw = {"kind": "model.completed", "payload": {"text": "正文", "data": {
        "truncated": True, "artifact": {"path": "/private", "sha256": "0" * 64},
    }}}
    assert readable_payload(raw, Reader()) == raw["payload"]
