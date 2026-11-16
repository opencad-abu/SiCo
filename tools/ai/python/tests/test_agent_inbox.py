"""Accepted quick input is durable, ordered and bound independently of conversation history."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest

from sico.core.contracts import BoundContext, ModelTurn, ToolCall, ToolResult
from sico.service.python_backend import PythonBackend
from sico.core.tools import ReadTool, ToolRegistry, no_arguments
from sico.providers.mock import ScriptedProvider
from sico.service.inbox import SessionInbox
from sico.service.launch import DesktopPipe
from sico.storage.journal import SessionJournal
from sico.transport.targets import TargetRegistry


def context(target="bound", cell="first"):
    return BoundContext(
        "instance", "generation", target, {"valid": True, "cellview": {"cell": cell}}
    )


def message(key="q1", cell="first"):
    return {
        "kind": "submit",
        "id": key,
        "text": "Read " + cell,
        "context": context(key, cell).record(),
    }


def test_durable_inbox_deduplicates_preserves_order_and_never_replays(tmp_path):
    with SessionJournal(tmp_path, "session") as journal:
        inbox = SessionInbox(journal, context())
        assert inbox.accept(message())
        assert not inbox.accept(message())
        assert inbox.accept(message("q2", "second"))
        with pytest.raises(ValueError, match="conflicts"):
            inbox.accept(message("q1", "other"))
        first = inbox.take()
        assert first[1].snapshot["cellview"]["cell"] == "first"
        assert inbox.take() is None
        inbox.finish({"id": "task1", "status": "completed"})
        second = inbox.take()
        assert second[1].snapshot["cellview"]["cell"] == "second"
        recovered = SessionInbox(journal, context())
        assert recovered.take() is None
        assert not recovered.accept(message("q2", "second"))
        record = json.loads((inbox.directory / "q2.json").read_text())
        assert record["status"] == "executing"


def test_inbox_capacity_and_cancel_queued_are_explicit(tmp_path):
    with SessionJournal(tmp_path, "session") as journal:
        inbox = SessionInbox(journal, context())
        for i in range(16):
            assert inbox.accept(message(f"q{i}"))
        with pytest.raises(ValueError, match="waiting"):
            inbox.accept(message("overflow"))
        assert not (inbox.directory / "overflow.json").exists()
        inbox.close()
        assert inbox.take() is None
        assert json.loads((inbox.directory / "q0.json").read_text())["status"] == "not_started"


def test_startup_pipe_keeps_multiple_inputs_in_order():
    pipe = DesktopPipe()
    pipe(message("q1"))
    pipe("show")
    pipe(message("q2", "second"))
    child = SimpleNamespace(stdin=io.BytesIO())
    registration = {"context": context().record()}
    pipe.attach(child, registration)
    records = [json.loads(line) for line in child.stdin.getvalue().splitlines()]
    assert records == [registration, message("q1"), {"kind": "show"}, message("q2", "second")]
    assert pipe.targets.get("q2").snapshot["cellview"]["cell"] == "second"


def test_target_registry_rejects_other_instance_and_changed_target():
    targets = TargetRegistry(context())
    targets.add(context("q1"))
    targets.add(context("q1"))
    with pytest.raises(ValueError, match="different data"):
        targets.add(context("q1", "second"))
    with pytest.raises(ValueError, match="another Virtuoso"):
        targets.add(BoundContext("other", "generation", "q2", {}))
    targets.release("q1")
    assert targets.get("q1") is None
    targets.release("bound")
    assert targets.get("bound") is not None


def test_conversation_keeps_history_while_each_task_freezes_its_target(tmp_path):
    observed = []
    tools = ToolRegistry()
    tools.register(
        ReadTool(
            "read",
            "read",
            {"type": "object"},
            no_arguments,
            lambda inputs, bound: observed.append(bound.record()) or ToolResult(),
        )
    )
    provider = ScriptedProvider(
        [
            [ModelTurn("", (ToolCall("c1", "read", {}),))],
            [ModelTurn("")],
            [ModelTurn("", (ToolCall("c2", "read", {}),))],
            [ModelTurn("")],
        ]
    )
    with SessionJournal(tmp_path, "session") as journal:
        loop = PythonBackend(provider, tools, journal, context())
        stream = loop.run("Read first", context=context("q1"), input_id="q1")
        assert next(stream)["kind"] == "task.started"
        with pytest.raises(RuntimeError):
            list(loop.run("second while busy", context=context("q2", "second")))
        assert list(stream)[-1]["kind"] == "task.completed"
        assert (
            list(loop.run("Read second", context=context("q2", "second"), input_id="q2"))[-1][
                "kind"
            ]
            == "task.completed"
        )
        assert [item["snapshot"]["cellview"]["cell"] for item in observed] == ["first", "second"]
        assert [m["content"] for m in loop.state.messages if m["role"] == "user"] == [
            "Read first",
            "Read second",
        ]
        starts = [event for event in journal.events() if event["kind"] == "task.started"]
        assert [event["payload"]["context"]["target_id"] for event in starts] == ["q1", "q2"]
