"""Goal continuation respects existing host and child input lifecycles."""

import queue

import pytest
from test_codex_child_input import (  # noqa: F401
    answer_child,
    child_running,
    ended,
    open_child,
    running,
)
from test_codex_thread_operations import goal
from wait_reads_fixture import backend, host_question, invoke, native_question, project, start

from sico.codex.rpc import RpcError
from sico.storage.journal import SessionJournal


def active_goal(loop):
    controls = loop.thread_ops.goals
    controls.running, controls.budget = True, 100
    controls.objective = goal()["objective"]
    controls.bound("turn")
    controls.capture(goal(), source="test")
    calls = []
    def request(method, params, **_kwargs):
        calls.append((method, params))
        if method == "thread/goal/set":
            return {"goal": {**controls.value, "status": params["status"]}}
        return {}
    loop.runtime.rpc.request = request
    return controls, calls


@pytest.mark.parametrize("kind", ["host", "native_blocking", "native_nonblocking"])
def test_pending_question_pauses_goal_and_retains_independent_reads(tmp_path, kind):
    project(tmp_path)
    with SessionJournal(tmp_path, "goal-question") as journal:
        loop = backend(journal, tmp_path)
        start(loop)
        controls, calls = active_goal(loop)
        row = (host_question(loop) if kind == "host" else
               native_question(loop, blocking=kind == "native_blocking"))
        def stop_after_read():
            assert controls.value["status"] == "paused"
            assert invoke(loop, "read_project_document", {"path": "docs/independent.txt"})[
                "status"] == "ok"
            assert invoke(loop, "get_context")["status"] == "waiting_user"
            raise RpcError("Test stops after observing wait")
        loop.runtime.rpc.next = stop_after_read
        try:
            with pytest.raises(RpcError, match="Test stops"):
                list(loop._consume_turn("turn", lambda: False))
            assert row["status"] == "pending" and len(calls) == 1
            assert calls[0][0] == "thread/goal/set"
        finally:
            loop.runtime = None
            loop.close()


@pytest.mark.parametrize("kind", ["native", "form", "url"])
def test_goal_does_not_advance_parent_while_child_input_is_pending(child_running, kind):  # noqa: F811
    loop, controller, sent = child_running
    controls, calls = active_goal(loop)
    row = open_child(loop, kind)
    frames = [ended("thread", "turn")]
    def next_frame():
        assert controls.value["status"] == "paused"
        if frames:
            return frames.pop(0)
        if row["status"] == "pending":
            answer_child(loop, controller, row)
            raise queue.Empty
        return ended()
    loop.runtime.rpc.next = next_frame
    events = list(loop._consume_turn("turn", lambda: False))
    assert events[-1]["kind"] == "task.completed"
    assert row["status"] == "answered" and len(sent) == 1
    assert not controls.continuing()
    assert not any(method == "turn/start" for method, _ in calls)
