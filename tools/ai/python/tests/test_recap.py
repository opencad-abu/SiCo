"""Recap history limits, admission and response validation."""

import json
from types import SimpleNamespace

import pytest

from sico.codex.recap_request import parse_response
from sico.service.recap_history import prompt


def test_prompt_is_bounded_utf8_and_preserves_latest_user_request():
    rows = [
        {"kind": "task.started", "task_id": "old", "payload": {"text": "Older task"}},
        {"kind": "model.completed", "task_id": "old", "payload": {"text": "Old answer"}},
        {"kind": "task.started", "task_id": "new", "payload": {"text": "最新目标" * 4000}},
        {"kind": "model.completed", "task_id": "new", "payload": {"text": '答复\\"' * 4000}},
        {"kind": "task.completed", "task_id": "new", "payload": {}},
    ]
    journal = SimpleNamespace(records=rows, replay_start=lambda _count: 1,
                             events=lambda *_a, **_kw: rows)
    result = prompt(journal)
    assert len(result.encode("utf-8")) < 12100
    content = json.loads(result.split("\n", 1)[1])
    assert any(row["text"].startswith("最新目标") for row in content)
    assert content[-1] == {"role": "status", "text": "completed"}


@pytest.mark.parametrize("value", ["", "plain text", '[]', '{}', '{"recap":7}',
                                   '{"recap":" "}', '{"recap":"ok","extra":true}'])
def test_invalid_response_is_not_displayed(value):
    with pytest.raises(ValueError):
        parse_response(value)


def test_response_stays_plain_bounded_text():
    assert parse_response(json.dumps({"recap": "  进展\n下一步  "})) == "进展 下一步"
    assert len(parse_response(json.dumps({"recap": "字" * 400}))) == 320
