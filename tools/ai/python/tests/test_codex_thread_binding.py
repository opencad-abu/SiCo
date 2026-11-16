"""Current identity commits and historical child ownership across forks."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest
from codex_thread_fixture import bind_thread
from sico.codex.rpc import RpcError
from sico.orchestration import ChildTaskPolicy
from sico.storage.journal import SessionJournal
from test_codex_turn_inputs import backend

POLICY = ChildTaskPolicy("original policy", "collect")


def identities(loop):
    return (loop.thread_id, loop.orchestration.parent_thread_id,
            loop.collaboration.parent_thread_id)


def collab(sender="parent", child="child", status="running", tool="spawnAgent"):
    return {"type": "collabAgentToolCall", "id": "collab", "tool": tool,
            "senderThreadId": sender, "receiverThreadIds": [child],
            "agentsStates": {child: {"status": status}}}


def test_start_resume_fork_keep_references_and_history(tmp_path):
    with SessionJournal(tmp_path, "identity") as journal:
        loop = backend(journal)
        registry, client = loop.orchestration, loop.collaboration
        assert identities(loop) == (None,) * 3
        bind_thread(loop, "parent")
        loop._record_collaboration(collab())
        child = registry.children["child"]
        bind_thread(loop, "parent", history_mode="paginated")
        assert loop.history_mode == "paginated"
        bind_thread(loop, "fork")
        assert identities(loop) == ("fork",) * 3
        assert loop.orchestration is registry and loop.collaboration is client
        assert registry.children["child"] is child
        assert child.parent_thread_id == "parent" and registry.active() == ()
        for obj, field in ((loop, "thread_id"), (loop, "history_mode"),
                           (registry, "parent_thread_id"), (client, "parent_thread_id"),
                           (child, "thread_id"), (child, "parent_thread_id")):
            with pytest.raises(AttributeError):
                setattr(obj, field, "overwrite")
        with pytest.raises(ValueError, match="owned"):
            client.send(child, "late")
        with pytest.raises(ValueError, match="owned"):
            registry.register("child", POLICY)
        with pytest.raises(ValueError, match="owned"):
            registry.update("child", "completed")
    with SessionJournal(tmp_path, "identity") as journal:
        restored = backend(journal)
        assert identities(restored) == ("fork",) * 3
        assert restored.child_tasks()[0].parent_thread_id == "parent"
        assert not restored.child_tasks()[0].task_id


@pytest.mark.parametrize("action,result", [
    ("resume", {"id": "wrong"}), ("fork", {"id": "parent"}),
    ("fork", {}), ("fork", None), ("fork", {"id": 7}), ("fork", {"id": " "}),
    ("start", {"id": "new"}),
])
def test_invalid_receipt_keeps_every_consumer_and_journal(tmp_path, action, result):
    with SessionJournal(tmp_path, "invalid") as journal:
        loop = backend(journal)
        bind_thread(loop, "parent")
        records = journal.events()
        with pytest.raises(RpcError, match="identity"):
            loop._bind_thread(action, result, expected="parent")
        assert identities(loop) == ("parent",) * 3
        assert journal.events() == records


@pytest.mark.parametrize("previous", [None, "parent"])
def test_failed_journal_commit_does_not_publish_identity(tmp_path, monkeypatch, previous):
    with SessionJournal(tmp_path, "write-failure") as journal:
        loop = backend(journal)
        if previous:
            bind_thread(loop, previous)
        def fail(*args):
            raise OSError("disk unavailable")
        monkeypatch.setattr(journal, "append", fail)
        with pytest.raises(OSError):
            bind_thread(loop, "new")
        assert identities(loop) == (previous,) * 3


def test_delayed_commit_cannot_overwrite_newer_identity(tmp_path):
    with SessionJournal(tmp_path, "stale") as journal:
        loop = backend(journal)
        bind_thread(loop, "parent")
        bind_thread(loop, "new")
        with pytest.raises(RpcError, match="changed"):
            loop._bind_thread("fork", {"id": "late"}, expected="parent")
        assert identities(loop) == ("new",) * 3


def test_late_events_and_pending_policies_do_not_cross_fork(tmp_path):
    with SessionJournal(tmp_path, "late") as journal:
        loop = backend(journal)
        bind_thread(loop, "parent")
        loop._record_collaboration(collab())
        child = loop.child_tasks()[0]
        loop.expect_child_task(POLICY)
        bind_thread(loop, "fork")
        loop._record_collaboration(collab(status="completed"))
        loop._record_collaboration(collab(sender="fork", status="completed"))
        loop._record_subagent({"agentThreadId": "child", "kind": "completed"}, live=True)
        loop._notification({"method": "turn/completed", "params": {
            "threadId": "child", "turn": {"id": "late", "status": "completed"}}})
        assert child.status == "running" and child.parent_thread_id == "parent"
        assert not child.task_id
        loop._record_collaboration(collab(sender="fork", child="new"))
        assert loop.orchestration.children["new"].policy != POLICY
        assert loop.orchestration.children["new"].parent_thread_id == "fork"
        loop.expect_child_task(POLICY)
        loop._record_collaboration(collab(sender="fork", child="planned"))
        assert loop.orchestration.children["planned"].policy == POLICY
        rows = [r["payload"] for r in journal.events() if r["kind"] == "codex.child"
                and r["payload"]["thread_id"] == "child"]
        assert {r["parent_thread_id"] for r in rows} == {"parent"}


def test_readers_and_child_registration_wait_for_durable_commit(tmp_path, monkeypatch):
    entered, release, attempted = Event(), Event(), Event()
    with SessionJournal(tmp_path, "serialized") as journal, ThreadPoolExecutor(2) as pool:
        loop = backend(journal)
        bind_thread(loop, "parent")
        append = journal.append
        def paused(*args):
            entered.set()
            assert release.wait(5)
            return append(*args)
        def register():
            attempted.set()
            return loop.collaboration.register("child", POLICY)
        monkeypatch.setattr(journal, "append", paused)
        switching = pool.submit(bind_thread, loop, "fork")
        try:
            assert entered.wait(5)
            registering = pool.submit(register)
            assert attempted.wait(5)
            assert not registering.done()
        finally:
            release.set()
        switching.result(5)
        assert registering.result(5).parent_thread_id == "fork"
        assert identities(loop) == ("fork",) * 3


@pytest.mark.parametrize("failure", ["rpc", "identity", "after_commit"])
def test_fork_failure_has_one_durable_binding_and_no_replay(tmp_path, monkeypatch, failure):
    with SessionJournal(tmp_path, "fork-failure") as journal:
        loop = backend(journal)
        bind_thread(loop, "parent")
        calls = []
        def request(method, params, **kwargs):
            calls.append(method)
            if failure == "rpc":
                raise RpcError("disconnected")
            return {"thread": {"id": "parent" if failure == "identity" else "fork"}}
        loop.runtime = SimpleNamespace(rpc=SimpleNamespace(request=request), close=lambda: None)
        loop.cancelled = lambda: False
        monkeypatch.setattr(loop.thread_ops.goals, "read", lambda rpc: None)
        monkeypatch.setattr(loop.thread_ops.goals, "pause", lambda *args: None)
        def fail(*args):
            raise RpcError("post-commit setup failed")
        monkeypatch.setattr(loop.thread_ops.goals, "before_resume", fail)
        with pytest.raises(RpcError):
            loop.thread_ops.start({"kind": "fork"})
        expected = "fork" if failure == "after_commit" else "parent"
        assert identities(loop) == (expected,) * 3
        assert calls == (["thread/fork", "thread/settings/update"]
                         if failure == "after_commit" else ["thread/fork"])
    with SessionJournal(tmp_path, "fork-failure") as journal:
        assert identities(backend(journal)) == (expected,) * 3
