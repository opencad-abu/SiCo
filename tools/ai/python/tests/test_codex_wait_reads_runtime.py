"""Original 0.154 app-server continues allowed MCP reads while a host answer is pending."""

import json

import pytest
from test_agent_codex_native import answer, gateway, native_codex, settings  # noqa: F401
from test_codex_child_input_runtime import call_named
from test_codex_elicitation_runtime import wait_for
from test_codex_resources_runtime import output, tool_call
from wait_reads_fixture import backend, project

from sico.codex.rpc import AppServer
from sico.service.controller import SessionController
from sico.core.session_events import EventCursor
from sico.storage.journal import SessionJournal


@pytest.mark.parametrize("tool_format", ["native", "flat"])
@pytest.mark.parametrize("outcome", ["cancel", "answer"])
def test_original_runtime_reads_while_question_pending(
    tmp_path, gateway, native_codex, monkeypatch, tool_format, outcome,  # noqa: F811
):
    project(tmp_path)
    methods, receipts = [], {}
    pending_observed = []
    original_request = AppServer.request

    def rpc(self, method, params, **kwargs):
        methods.append(method)
        return original_request(self, method, params, **kwargs)

    monkeypatch.setattr(AppServer, "request", rpc)

    def call(name, args, call_id):
        return lambda request: tool_call(request, "mcp__sico_ic", name, call_id, args)

    gateway.responses.put(call("decision_probe", {}, "question"))
    gateway.responses.put(call("read_project_document", {"path": "docs/independent.txt"}, "read"))

    def after_read(request):
        receipts["read"] = output(request, "read")
        with loop.lock:
            pending_observed.append(bool(loop.audit.pending))
        return tool_call(request, "mcp__sico_ic", "read_project_document", "dependent",
                         {"path": "docs/pdk.txt"})

    def after_dependent(request):
        receipts["dependent"] = output(request, "dependent")
        if outcome == "answer":
            with loop.lock:
                questions = next(iter(loop.audit.index.audits.values()))["questions"]
            return call_named(request, "request_user_input", "answer", {"questions": questions})
        return answer("Independent document read; dependent PDK data waits for your answer.")

    def after_answer(request):
        receipts["answer"] = output(request, "answer")
        return tool_call(request, "mcp__sico_ic", "read_project_document", "explicit-read",
                         {"path": "docs/pdk.txt"})

    def completed(request):
        receipts["explicit-read"] = output(request, "explicit-read")
        return answer("PDK document read after the original user response.")

    gateway.responses.put(after_read)
    gateway.responses.put(after_dependent)
    if outcome == "answer":
        gateway.responses.put(after_answer)
        gateway.responses.put(completed)
    with SessionJournal(tmp_path, "wait-runtime") as journal:
        loop = backend(journal, tmp_path, settings(gateway, native_codex, tool_format))
        controller = SessionController(loop)
        try:
            controller.submit("Read independent project evidence while clarifying the PDK.")
            wait_for(lambda: "dependent" in receipts, timeout=15)
            with loop.lock:
                assert loop.audit.pending and loop.state.task["status"] == "executing"
                row = next(iter(loop.audit.index.audits.values()))
                assert row["status"] == "pending"
            assert pending_observed == [True]
            assert "Evidence for independent" in str(receipts["read"])
            assert "waiting_user" in str(receipts["dependent"])
            assert "Evidence for pdk" not in str(receipts["dependent"])
            assert methods.count("turn/start") == 1
            assert not any(method.startswith("account/") for method in methods)
            assert not (loop.runtime.home / "auth.json").exists()
            if outcome == "answer":
                def bound():
                    with loop.lock:
                        return any(p["rpc_id"] is not None for p in loop.audit.pending.values())
                wait_for(bound, timeout=10)
                controller.backend_commands.answer_audit(controller.session_id, row["task_id"], row["id"],
                                        {"pdk_library": {"choice": "first", "text": ""}}, "reply")
            else:
                controller.cancel()
            assert controller.wait(8)
            assert loop.state.task["status"] == (
                "cancelled" if outcome == "cancel" else "completed")
            if outcome == "answer":
                assert "first" in str(receipts["answer"])
                assert "Evidence for pdk" in str(receipts["explicit-read"])
                assert row["status"] == "answered" and methods.count("turn/start") == 1
            assert not controller.inbox.pending and not loop.audit.pending
            EventCursor(controller.session_id, controller.runtime_id, loop.state.context).consume(
                controller.updates.read_updates())
            assert len([r for r in journal.events() if r["kind"] == "task.started"]) == 1
        finally:
            (tmp_path / "wait-reads-proof.json").write_text(json.dumps({
                "methods": methods, "receipts": receipts, "pending_during_read": pending_observed,
                "task": loop.state.task, "events": journal.events(),
            }, ensure_ascii=False, indent=2))
            controller.close()
            assert controller.wait(8)
