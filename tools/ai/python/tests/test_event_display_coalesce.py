"""Coalescing stays linear across long model-delta segments."""

import random
import time

from sico.service.event_display import coalesce


def event(sequence, kind, task_id="task", text=None):
    payload = {}
    if text is not None:
        payload["text"] = text
    return {"sequence": sequence, "kind": kind, "task_id": task_id, "payload": payload}


def reference(events):
    statuses, skipped = {}, set()
    for index in range(len(events) - 1, -1, -1):
        row = events[index]
        key, kind = row.get("task_id"), row["kind"]
        if kind in {"model.status", "router.status"}:
            statuses.setdefault((key, kind), index)
    for index, row in enumerate(events):
        if row["kind"] != "model.delta":
            continue
        task_id = row.get("task_id")
        for following in events[index + 1:]:
            if following.get("task_id") != task_id:
                continue
            if following["kind"] in {"task.started", "tool.started"}:
                break
            if (following["kind"] == "model.completed"
                    and following["payload"].get("text")):
                skipped.add(index)
                break
    return [row for index, row in enumerate(events)
            if index not in skipped
            and statuses.get((row.get("task_id"), row["kind"]), index) == index]


def test_coalesce_preserves_completion_and_tool_boundaries():
    rows = [
        event(1, "model.delta", text="old"),
        event(2, "model.completed", text="old answer"),
        event(3, "tool.started"),
        event(4, "model.delta", text="new"),
        event(5, "model.completed", text="new answer"),
        event(6, "model.delta", task_id="other", text="other"),
        event(7, "model.completed", task_id="other", text="other answer"),
    ]
    assert [row["sequence"] for row in coalesce(rows)] == [2, 3, 5, 7]
    rows[4]["payload"]["text"] = ""
    assert [row["sequence"] for row in coalesce(rows)] == [2, 3, 4, 5, 7]


def test_coalesce_is_linear_for_a_long_delta_segment():
    rows = [event(index, "model.delta", text="x") for index in range(1, 5001)]
    rows.append(event(5001, "model.completed", text="answer"))
    started = time.perf_counter()
    kept = coalesce(rows)
    assert time.perf_counter() - started < 0.5
    assert [row["sequence"] for row in kept] == [5001]


def test_linear_coalesce_matches_the_forward_reference_randomized():
    generator = random.Random(11)
    kinds = ("model.delta", "model.completed", "model.status", "router.status",
             "tool.started", "task.started", "tool.finished")
    for length in range(40):
        rows = []
        for sequence in range(1, length + 1):
            kind = generator.choice(kinds)
            rows.append(event(sequence, kind,
                              task_id=generator.choice(("a", "b")),
                              text=generator.choice(("", "answer"))))
        assert coalesce(rows) == reference(rows)
