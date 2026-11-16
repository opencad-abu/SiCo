"""Native start/resume results cannot publish an invalid or partial binding."""

from types import SimpleNamespace

import pytest
import sico.codex.runtime_assembly as runtime_module
from codex_thread_fixture import bind_thread
from sico.codex.rpc import RpcError
from sico.storage.journal import SessionJournal
from test_codex_thread_binding import identities
from test_codex_turn_inputs import backend


def connection(loop, monkeypatch, response):
    calls, closed = [], []
    def request(method, params, **kwargs):
        calls.append((method, params))
        if isinstance(response, Exception):
            raise response
        return response
    runtime = SimpleNamespace(rpc=SimpleNamespace(request=request), cwd="/tmp",
                              close=lambda: closed.append(True))
    monkeypatch.setattr(runtime_module, "CodexRuntime", lambda *args, **kwargs: runtime)
    monkeypatch.setattr(runtime_module, "ToolBridge", lambda *args, **kwargs:
                        SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(loop.resources, "register", lambda rpc: None)
    monkeypatch.setattr(loop.thread_ops.goals, "before_resume", lambda rpc: None)
    monkeypatch.setattr(loop.thread_ops.memory, "apply", lambda rpc: None)
    return calls, closed


@pytest.mark.parametrize("previous", [None, "parent"])
@pytest.mark.parametrize("fault", ["rpc", "missing", "empty", "wrong", "journal"])
def test_connection_failure_keeps_old_identity_and_closes_runtime(
    tmp_path, monkeypatch, previous, fault,
):
    with SessionJournal(tmp_path, "failed") as journal:
        loop = backend(journal)
        if previous:
            loop.resources.prepare()
            bind_thread(loop, previous)
        response = {"thread": {"id": previous or "started"}}
        if fault == "rpc":
            response = RpcError("disconnected")
        elif fault == "missing":
            response = {}
        elif fault == "empty":
            response = {"thread": {"id": ""}}
        elif fault == "wrong":
            response = {"thread": {"id": "wrong" if previous else 42}}
        calls, closed = connection(loop, monkeypatch, response)
        if fault == "journal":
            append = journal.append
            def fail(kind, *args):
                if kind in {"codex.thread", "codex.connection"}:
                    raise OSError("disk unavailable")
                return append(kind, *args)
            monkeypatch.setattr(journal, "append", fail)
        with pytest.raises((RpcError, OSError)):
            loop._ensure_runtime()
        assert identities(loop) == (previous,) * 3
        assert loop.runtime is None and loop.bridge is None and closed == [True]
        assert len(calls) == (0 if fault == "journal" else 1)


@pytest.mark.parametrize("previous", [None, "parent"])
def test_connection_commits_once_and_resumes_same_owner(tmp_path, monkeypatch, previous):
    with SessionJournal(tmp_path, "connected") as journal:
        loop = backend(journal)
        if previous:
            loop.resources.prepare()
            bind_thread(loop, previous)
        thread = previous or "started"
        calls, closed = connection(loop, monkeypatch, {
            "thread": {"id": thread, "historyMode": "legacy", "turns": []}})
        loop._ensure_runtime()
        assert identities(loop) == (thread,) * 3
        assert len(calls) == (2 if previous else 3)
        assert calls[0][0] == ("thread/resume" if previous else "thread/start")
        assert calls[0][1]["modelProvider"] == "sico"
        if not previous:
            assert calls[1][0] == "thread/inject_items"
            assert calls[1][1]["threadId"] == thread
        assert calls[-1][0] == "thread/settings/update"
        assert calls[-1][1]["threadId"] == thread
        loop._ensure_runtime()
        assert len(calls) == (2 if previous else 3)
        loop.close()
        assert closed == [True]
        assert [r["payload"]["thread_id"] for r in journal.events()
                if r["kind"] == "codex.thread"] == [thread]


def test_initial_history_write_failure_does_not_publish_a_thread(tmp_path, monkeypatch):
    with SessionJournal(tmp_path, "unpersisted") as journal:
        loop = backend(journal)
        calls, closed = connection(loop, monkeypatch, {"thread": {"id": "started"}})
        from sico.codex import thread_startup

        original = thread_startup.persist_initial_source
        def reject(rpc, thread, context):
            def failed(method, params):
                calls.append((method, params))
                raise RpcError("initial history write failed")
            original(SimpleNamespace(request=failed), thread, context)
        monkeypatch.setattr(thread_startup, "persist_initial_source", reject)
        with pytest.raises(RpcError, match="initial history write failed"):
            loop._ensure_runtime()
        assert identities(loop) == (None, None, None)
        assert loop.runtime is None and closed == [True]
        assert [call[0] for call in calls] == ["thread/start", "thread/inject_items"]
        assert not any(row["kind"] == "codex.thread" for row in journal.events())
