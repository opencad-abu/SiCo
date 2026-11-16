"""Queued work, child questions, cancellation and recovery retain their boundaries."""

import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_codex_child_input import register as register_child
from test_codex_child_input import request
from wait_reads_fixture import backend, host_question, invoke, native_question, project, start

from sico.codex.rpc import RpcError
from sico.core.contracts import ToolResult
from sico.core.read_dependencies import reference_dependencies
from sico.core.tools import Tool, no_arguments
from sico.storage.journal import SessionJournal


@pytest.mark.parametrize("boundary", ["question", "cancel", "context", "manifest"])
def test_registry_queue_rechecks_before_entering_handler(tmp_path, boundary):
    project(tmp_path)
    with SessionJournal(tmp_path, "queue") as journal, ThreadPoolExecutor(1) as pool:
        loop = backend(journal, tmp_path)
        start(loop)
        if boundary == "manifest":
            host_question(loop)
        tools, entered, called = loop.tools, threading.Event(), []
        tools.routed = False
        name = "read_project_document" if boundary == "manifest" else "get_context"
        original = tools.resolve(name)
        tools._tools[name] = replace(
            original, validate=lambda args: entered.set(),
            handler=lambda args, context: called.append(True) or ToolResult())
        lock = threading.Lock()
        tools._locks[loop.state.context.instance_id] = lock
        lock.acquire()
        future = pool.submit(invoke, loop, name, {"path": "docs/independent.txt"}
                             if boundary == "manifest" else {})
        try:
            assert entered.wait(2)
            if boundary == "question":
                native_question(loop)
            elif boundary == "cancel":
                loop._tool_stop.set()
            elif boundary == "context":
                loop.state.context.snapshot["changed"] = True
            else:
                from sico.core.read_dependencies import MANIFEST
                from sicostate import project_root

                (project_root(tmp_path) / MANIFEST).write_text("{}")
            lock.release()
            result = future.result(3)
            assert result["status"] == ("waiting_user" if boundary in {"question", "manifest"}
                                         else "cancelled_before_start")
            assert not called and not loop.state.task["pending_calls"]
        finally:
            if lock.locked():
                lock.release()
            loop.runtime = None
            loop.close()


@pytest.mark.parametrize("kind", ["native", "form", "url"])
def test_child_questions_allow_only_independent_reads_and_keep_source(tmp_path, kind):
    project(tmp_path)
    with SessionJournal(tmp_path, "child") as journal:
        loop = backend(journal, tmp_path)
        start(loop)
        register_child(loop)
        assert loop._input_request(request(kind), "turn")
        row = next(reversed(loop.audit.index.audits.values()))
        try:
            assert invoke(loop, "read_project_document", {"path": "docs/independent.txt"})[
                "status"] == "ok"
            assert invoke(loop, "read_project_document", {"path": "docs/parameters.txt"})[
                "status"] == "waiting_user"
            assert row["binding"]["child_thread_id"] == "child"
            loop.orchestration.children["child"].input_closed = True
            assert invoke(loop, "read_project_document", {"path": "docs/independent.txt"})[
                "status"] == "waiting_user"
        finally:
            loop.runtime = None
            loop.close()


def test_recovery_replays_neither_read_nor_question_and_retains_receipt(tmp_path):
    project(tmp_path)
    with SessionJournal(tmp_path, "recovery") as journal:
        loop = backend(journal, tmp_path)
        start(loop)
        host_question(loop)
        assert invoke(loop, "read_project_document", {"path": "docs/independent.txt"})[
            "status"] == "ok"
        events = journal.events()
        loop.runtime = None
        loop.close()
    with SessionJournal(tmp_path, "recovery") as journal:
        loop = backend(journal, tmp_path)
        try:
            assert not loop.audit.pending and not loop._inflight
            assert len([r for r in journal.events() if r["kind"] == "tool.started"]) == len(
                [r for r in events if r["kind"] == "tool.started"])
            assert loop.state.task["status"] == "needs_reconcile"
            assert any(r.get("tool") == "read_project_document"
                       for r in loop.workbench.index.data.values())
        finally:
            loop.close()


def test_read_inflight_when_task_ends_cannot_resume_or_move_receipt(tmp_path):
    entered, release = threading.Event(), threading.Event()
    project(tmp_path)
    with SessionJournal(tmp_path, "late") as journal, ThreadPoolExecutor(1) as pool:
        loop = backend(journal, tmp_path)
        start(loop)
        host_question(loop)

        def read(args, context):
            entered.set()
            assert release.wait(3)
            return ToolResult(data="Independent evidence")

        loop.tools.register(Tool("slow_read", "Bounded test read", {"type": "object"},
                                 no_arguments, read, execution_domain="agent", effect="read",
                                 input_dependencies=reference_dependencies))
        future = pool.submit(invoke, loop, "slow_read")
        try:
            assert entered.wait(2)
            loop._finish("cancelled")
            release.set()
            assert future.result(3)["status"] == "ok"
            assert loop.state.task["status"] == "needs_reconcile"
            assert journal.events()[-1]["kind"] == "session.tool_receipt"
            assert not loop.audit.pending and not loop._inflight
        finally:
            release.set()
            loop.runtime = None
            loop.close()


def test_waiting_for_answer_does_not_disable_the_running_read_timeout(tmp_path, monkeypatch):
    project(tmp_path)
    with SessionJournal(tmp_path, "timeout") as journal:
        loop = backend(journal, tmp_path)
        start(loop)
        host_question(loop)
        loop.provider = SimpleNamespace(tool_timeout=1, idle_timeout=1)
        clock = [0]
        monkeypatch.setattr("sico.codex.backend.time", SimpleNamespace(
            monotonic=lambda: clock[0], strftime=time.strftime, gmtime=time.gmtime))
        loop.state.task["pending_calls"] = [{"id": "read", "name": "read_project_document",
                                            "input": {"path": "docs/independent.txt"}}]
        loop.tool_started_at["read"] = 0

        def next_event():
            clock[0] = 2
            raise queue.Empty

        loop.runtime.rpc.next = next_event
        try:
            with pytest.raises(RpcError, match="工具执行超过配置时限"):
                list(loop._consume_turn("turn", lambda: False))
            assert loop.audit.pending
        finally:
            loop.runtime = None
            loop.close()
