"""Task-level usage summarization over private session records."""

import json

import pytest

from mcp_session_usage import RecordTooLarge, read_events, summarize


def event(sequence, kind, *, task_id="t1", payload=None, timestamp=None):
    return {
        "contract": "agent_task.v1",
        "sequence": sequence,
        "session_id": "s",
        "task_id": task_id,
        "timestamp": timestamp or f"2026-10-09T09:00:{sequence:02d}+00:00",
        "kind": kind,
        "payload": payload or {},
    }


def write_events(session, rows):
    session.mkdir(parents=True, exist_ok=True)
    (session / "events.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8")


def write_rollout(session, rounds):
    path = session / "codex/sessions/2026/10/09"
    path.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"type": "session_meta", "payload": {}})]
    for index, round_ in enumerate(rounds, start=1):
        lines.append(json.dumps({
            "type": "event_msg",
            "payload": {"type": "token_count", "info": {
                "total_token_usage": round_["total"],
                "last_token_usage": round_["last"],
                "turn_id": f"turn-{index}",
            }},
        }))
    (path / "rollout-2026-10-09T09-00-00-abc.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")


def sample_events():
    return [
        event(1, "task.started", payload={"text": "打开library manager"}),
        event(2, "token.usage", payload={
            "input_tokens": 1000, "output_tokens": 50,
            "context_usage": {"used": 1100, "capacity": 200000}}),
        event(3, "tool.started", payload={"name": "get_project_context", "input": {}}),
        event(4, "token.usage", payload={
            "input_tokens": 3000, "output_tokens": 120,
            "context_usage": {"used": 4000, "capacity": 200000}}),
        event(5, "tool.started", payload={"name": "eval_skill", "input": {}}),
        event(6, "token.usage", payload={
            "input_tokens": 6000, "output_tokens": 200,
            "context_usage": {"used": 5200, "capacity": 200000}}),
        event(7, "task.completed", payload={"message": ""}),
    ]


def sample_rollout():
    return [
        {"total": {"input_tokens": 1000, "cached_input_tokens": 800,
                   "cache_write_input_tokens": 0, "output_tokens": 50},
         "last": {"input_tokens": 1000, "cached_input_tokens": 800,
                  "cache_write_input_tokens": 0, "output_tokens": 50}},
        {"total": {"input_tokens": 3000, "cached_input_tokens": 2800,
                   "cache_write_input_tokens": 0, "output_tokens": 120},
         "last": {"input_tokens": 2000, "cached_input_tokens": 2000,
                  "cache_write_input_tokens": 0, "output_tokens": 70}},
        {"total": {"input_tokens": 6000, "cached_input_tokens": 5800,
                   "cache_write_input_tokens": 0, "output_tokens": 200},
         "last": {"input_tokens": 3000, "cached_input_tokens": 3000,
                  "cache_write_input_tokens": 0, "output_tokens": 80}},
    ]


def test_matched_rollout_reports_rounds_peak_and_cache(tmp_path):
    session = tmp_path / "session"
    write_events(session, sample_events())
    write_rollout(session, sample_rollout())
    summary = summarize(session)
    assert summary["rollout"]["alignment"] == "matched"
    task = summary["tasks"][0]
    assert task["model_rounds"] == 3
    assert task["tool_calls"] == 2
    assert task["tools"] == {"eval_skill": 1, "get_project_context": 1}
    assert task["input_tokens"] == 6000
    assert task["peak_request_input_tokens"] == 3000
    assert task["output_tokens"] == 200
    assert task["cached_input_tokens"] == 5800
    assert task["cache_write_input_tokens"] == 0
    assert task["context_peak"] == 5200
    assert task["context_capacity"] == 200000
    assert task["outcome"] == "completed"
    assert task["duration_seconds"] == 6.0
    assert summary["session"]["cached_input_tokens"] == 5800


def test_rollout_count_mismatch_is_not_measured(tmp_path):
    session = tmp_path / "session"
    write_events(session, sample_events())
    write_rollout(session, sample_rollout()[:2])
    summary = summarize(session)
    assert summary["rollout"]["alignment"] == "count-mismatch"
    assert summary["tasks"][0]["cached_input_tokens"] is None
    assert summary["session"]["cached_input_tokens"] is None
    assert summary["session"]["input_tokens"] == 6000


def test_missing_rollout_reports_null_cache_and_incomplete_task(tmp_path):
    session = tmp_path / "session"
    rows = [row for row in sample_events() if row["kind"] != "task.completed"]
    write_events(session, rows)
    summary = summarize(session)
    assert summary["rollout"]["alignment"] == "no-rollout"
    task = summary["tasks"][0]
    assert task["cached_input_tokens"] is None
    assert task["outcome"] == "incomplete"
    assert task["ended"] is None and task["duration_seconds"] is None


def test_tasks_are_reported_in_order_with_independent_counters(tmp_path):
    session = tmp_path / "session"
    rows = sample_events() + [
        event(8, "task.started", task_id="t2", payload={"text": "第二个任务"},
              timestamp="2026-10-09T09:10:00+00:00"),
        event(9, "token.usage", task_id="t2",
              payload={"input_tokens": 500, "output_tokens": 10,
                       "context_usage": {"used": 700, "capacity": 200000}},
              timestamp="2026-10-09T09:10:01+00:00"),
        event(10, "task.failed", task_id="t2", payload={"message": "boom"},
              timestamp="2026-10-09T09:10:02+00:00"),
    ]
    write_events(session, rows)
    summary = summarize(session)
    assert [task["task_id"] for task in summary["tasks"]] == ["t1", "t2"]
    assert summary["tasks"][1]["outcome"] == "failed"
    assert summary["tasks"][1]["text"] == "第二个任务"
    assert summary["session"]["input_tokens"] == 6500
    assert summary["session"]["outcomes"] == {"completed": 1, "failed": 1}


def test_oversized_journal_is_refused_before_reading(tmp_path):
    session = tmp_path / "session"
    write_events(session, sample_events())
    with pytest.raises(RecordTooLarge):
        read_events(session, limit=16)
    with pytest.raises(RecordTooLarge):
        summarize(session, limit=16)
