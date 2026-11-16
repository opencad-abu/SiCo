"""Prepared decisions bind to native input without leaving invisible blockers."""

from copy import deepcopy
import queue
from types import SimpleNamespace

import pytest

from sico.codex.backend import CodexBackend
from sico.service.workbench import attach_workbench
from sico.storage.journal import SessionJournal
from test_agent_codex_native import CONTEXT, registry


@pytest.fixture
def backend(tmp_path):
    with SessionJournal(tmp_path, "audit-lifecycle") as journal:
        loop = CodexBackend(None, registry([]), journal, CONTEXT)
        loop.workbench = attach_workbench(loop)
        loop._begin("Review LDO inputs", CONTEXT, "input", "chat")
        loop.active, loop.cancelled = True, lambda: False
        loop.workbench.stage({"work_title": "LDO", "stage_title": "Specification",
                              "objective": "Resolve inputs", "acceptance": "Inputs answered"}, CONTEXT)
        sent = []
        loop.runtime = SimpleNamespace(rpc=SimpleNamespace(send=lambda message, **kwargs: sent.append(message)))
        yield loop, sent
        loop.runtime = None
        loop.close()


def prepare(loop, request="scope", review_class="blocking"):
    evidence = next(iter(loop.workbench.index.data))
    return loop.audit.prepare({
        "request_id": request, "title": "LDO input", "recommendation": "Use supplied target",
        "rationale": "Target is bound", "evidence_ids": [evidence],
        "review_class": review_class,
        "questions": [{"id": request, "header": "LDO", "question": "Which scope?",
                       "options": [{"label": "Inspect (Recommended)", "description": "Read device CDF"},
                                   {"label": "Design", "description": "Prepare/prepare a new target"}]}],
    }, CONTEXT).data


def native_input(questions, rpc_id=41):
    return {"id": rpc_id, "params": {"threadId": "thread", "turnId": "turn",
            "itemId": "item_" + str(rpc_id), "isBlocking": False, "questions": questions}}


def test_changed_option_description_reuses_evidence_and_allows_next_audit(backend):
    loop, sent = backend
    prepared = prepare(loop)
    incoming = deepcopy(prepared["questions"])
    incoming[0]["options"][1]["description"] = "Prepare a new target"
    incoming[0]["options"].reverse()
    event = native_input(incoming)
    loop.audit.open(event)
    loop.audit.open(event)
    assert list(loop.audit.pending) == [prepared["audit_id"]]
    row = loop.audit.index.audits[prepared["audit_id"]]
    assert row["questions"] == prepared["questions"]
    assert row["recommendation"] == "Use supplied target"
    loop.audit.answer(row["id"], {"scope": {"choice": "Inspect (Recommended)", "text": ""}},
                      "reply", "user", row["task_id"])
    assert loop.audit.deliver() == 1
    assert row["status"] == "answered" and not loop.audit.pending
    assert sent == [{"id": 41, "result": {"answers": {
        "scope": {"answers": ["Inspect (Recommended)"]}}}}]
    second = prepare(loop, "domain")
    loop.audit.open(native_input(second["questions"], rpc_id=42))
    assert list(loop.audit.pending) == [second["audit_id"]]
    assert len(loop.audit.index.audits) == 2
    assert all(row["status"] != "prepared" for row in loop.audit.index.audits.values())
    assert len([e for e in loop.journal.events() if e["kind"] == "workbench.audit.opened"]) == 2


@pytest.mark.parametrize("change", ["question", "option", "combined"])
def test_different_decision_does_not_orphan_prepared_audit(backend, change):
    loop, _ = backend
    prepared = prepare(loop)
    incoming = deepcopy(prepared["questions"])
    if change == "question":
        incoming[0]["question"] = "Delete this design?"
    elif change == "option":
        incoming[0]["options"][1]["label"] = "Delete"
    else:
        incoming.append({**deepcopy(incoming[0]), "id": "domain"})
    with pytest.raises(ValueError, match="conflicts with the prepared audit"):
        loop.audit.open(native_input(incoming))
    assert list(loop.audit.index.audits) == [prepared["audit_id"]]
    assert not loop.audit.pending
    loop.audit.open(native_input(prepared["questions"], rpc_id=42))
    assert list(loop.audit.pending) == [prepared["audit_id"]]


def test_supplementary_question_releases_native_input_without_waiting(backend):
    loop, sent = backend
    prepared = prepare(loop, "preference", review_class="supplementary")
    loop.audit.open(native_input(prepared["questions"], rpc_id=43))
    row = loop.audit.index.audits[prepared["audit_id"]]
    assert row["review_class"] == "supplementary"
    assert loop.state.task["supplementary_audits"] == [row["id"]]
    assert "waiting_audits" not in loop.state.task
    assert not loop.audit.waiting_for_input()

    assert loop.audit.answer_supplementary() == 1
    assert row["status"] == "answered"
    assert row["reply"]["automatic"] is True and row["reply"]["answers"] == {}
    assert sent == [{"id": 43, "result": {"answers": {}}}]
    assert "supplementary_audits" not in loop.state.task
    with pytest.raises(ValueError, match="已结束|已答复"):
        loop.audit.answer(row["id"], {"preference": {"choice": "Inspect (Recommended)",
                                                    "text": ""}}, "late", "user", row["task_id"])


def test_blocking_declaration_wins_over_nonblocking_wire_hint(backend):
    loop, sent = backend
    prepared = prepare(loop, "required", review_class="blocking")
    loop.audit.open(native_input(prepared["questions"], rpc_id=44))
    row = loop.audit.index.audits[prepared["audit_id"]]
    assert row["review_class"] == "blocking"
    assert loop.state.task["waiting_audits"] == [row["id"]]
    assert "supplementary_audits" not in loop.state.task
    assert loop.audit.waiting_for_input()
    assert loop.audit.answer_supplementary() == 0
    assert not sent and row["status"] == "pending"


def test_turn_loop_releases_supplementary_question_before_completion(backend):
    loop, sent = backend
    from codex_thread_fixture import bind_thread

    bind_thread(loop)
    loop.runtime.close = lambda: None
    loop.runtime.rpc.request = lambda *args, **kwargs: {}
    prepared = prepare(loop, "preference", review_class="supplementary")
    loop.audit.open(native_input(prepared["questions"], rpc_id=45))
    frames = [
        {"method": "item/completed", "params": {
            "threadId": "thread", "turnId": "turn",
            "item": {"id": "message", "type": "agentMessage", "text": "继续完成。"},
        }},
        {"method": "turn/completed", "params": {
            "threadId": "thread", "turnId": "turn",
            "turn": {"id": "turn", "status": "completed"},
        }},
    ]
    loop.runtime.rpc.next = lambda: frames.pop(0) if frames else (_ for _ in ()).throw(queue.Empty())
    events = list(loop._turn("turn", lambda: False))
    assert sent == [{"id": 45, "result": {"answers": {}}}]
    assert events[-1]["kind"] == "task.completed"
    row = loop.audit.index.audits[prepared["audit_id"]]
    assert row["status"] == "answered" and row["reply"]["automatic"] is True


@pytest.mark.parametrize('changed', [False, True])
def test_native_answer_waits_for_source_recovery_before_delivery(backend, monkeypatch, changed):
    from sico.core.target_validation import TargetUnconfirmed
    from sico.core.contracts import NeedsReconcile

    loop, sent = backend
    prepared = prepare(loop)
    loop.audit.open(native_input(prepared['questions']))
    row = loop.audit.index.audits[prepared['audit_id']]
    loop.audit.answer(row['id'], {'scope': {'choice': 'Inspect (Recommended)', 'text': ''}},
                      'reply', 'user', row['task_id'])
    original = loop.validate_audit_target
    def unavailable():
        raise TargetUnconfirmed('temporary source read')
    monkeypatch.setattr(loop, 'validate_audit_target', unavailable)
    for _ in range(2):
        assert loop.audit.deliver() == 0
        assert row['status'] == 'answer_received' and row['id'] in loop.audit.pending
        assert not sent and not loop.stale
    if changed:
        def invalid():
            raise NeedsReconcile('confirmed source change')
        monkeypatch.setattr(loop, 'validate_audit_target', invalid)
    else:
        monkeypatch.setattr(loop, 'validate_audit_target', original)
    assert (loop.audit.deliver() or 0) == (0 if changed else 1)
    assert row['status'] == ('invalid' if changed else 'answered')
    assert len(sent) == (0 if changed else 1)
    assert not loop.audit.pending
    assert loop.stale == changed
