"""Question identity and native record budgets survive the service boundary move."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from sico.core.audit_questions import bounded, questions, same_host_questions
from sico.codex.input_records import native_record
from test_codex_access import answer, request
from test_codex_child_input import register
from test_codex_elicitation import running  # noqa: F401


def question(**values):
    return {"id": "scope", "header": "范围", "question": "检查范围？", "options": [
        {"label": "全部", "description": "完整检查"}], **values}


def test_question_normalization_detaches_and_keeps_presentation_identity():
    source = [question(extra="not part of the record")]
    original = deepcopy(source)
    normalized = questions(source)
    assert source == original and "extra" not in normalized[0]
    source[0]["options"][0]["description"] = "changed"
    assert normalized[0]["options"][0]["description"] == "完整检查"
    shown = questions([question(header="新标题", options=[
        {"label": "全部 (Recommended)", "description": "另一种展示"}])])
    assert same_host_questions(normalized, shown)
    shown[0]["question"] += "changed"
    assert not same_host_questions(normalized, shown)
    assert not same_host_questions(normalized, questions([question(options=[
        {"label": "全部", "description": "first"},
        {"label": "全部 (Recommended)", "description": "duplicate after normalization"}])]))


@pytest.mark.parametrize("value", [[], [question()] * 4, [question()] * 2,
    [question(isSecret=True)], [question(id="bad id")], [question(header="h" * 81)],
    [question(question="q" * 2001)], [question(options=[{"label": "x", "description": "y"}] * 7)],
    [question(options=[{"label": "x", "description": "y"}] * 2)]])
def test_invalid_questions_rejected(value):
    with pytest.raises(ValueError):
        questions(value)


@pytest.mark.parametrize("value", [None, "", "   ", "x" * 5, "a\x00", "a\x7f"])
def test_bounded_rejects_invalid_text(value):
    with pytest.raises(ValueError):
        bounded(value, 4)


def test_question_limits_and_existing_empty_options_default():
    value = question(header="h" * 80, question="q" * 2000, options=[
        {"label": str(i) + "l" * 199, "description": "d" * 1000} for i in range(6)])
    assert len(questions([value])[0]["options"]) == 6
    assert questions([question(options=None)])[0]["options"] == []
    assert bounded("\n\r\t", 3, empty=True) == "\n\r\t"


def test_record_projection_preserves_identity_and_does_not_mutate_inputs():
    incoming, context = questions([question()]), {"target": {"cell": "dut"}}
    current = {"work_id": "work", "stage_id": "stage", "source": "source"}
    child = SimpleNamespace(thread_id="child", parent_thread_id="parent",
                            policy=SimpleNamespace(name="检查"))
    origin = {"task_id": "task", "thread_id": "child", "turn_id": "child-turn"}
    audits = [{"task_id": "old"}] * 40 + [{"task_id": "task"}] * 31
    before = deepcopy((incoming, context, current, audits, vars(child)))
    row = native_record(incoming, "task", context, current, "digest", child,
                        "child-turn", iter(audits), 3, lambda value: origin if value is child else None)
    assert row.pop("id").startswith("a_")
    assert row == {"title": "检查 · 范围", "questions": incoming, "recommendation": "",
            "rationale": "", "task_id": "task", "work_id": "work", "stage_id": "stage",
            "review_class": "blocking",
            "context": context, "evidence": [{"id": "source", "digest": "digest"}],
        "child_thread_id": "child", "parent_thread_id": "parent", "child_turn_id": "child-turn",
        "child_name": "检查", "input_origin": origin}
    assert (incoming, context, current, audits, vars(child)) == before


@pytest.mark.parametrize("count,pending", [(32, 0), (0, 4)])
def test_child_record_budget_rejects_before_capturing_origin(count, pending):
    child = SimpleNamespace(thread_id="child", parent_thread_id="parent", policy=SimpleNamespace(name="child"))
    with pytest.raises(ValueError, match="Child input limit reached"):
        native_record(questions([question()]), "task", {},
            {"work_id": "work", "stage_id": "stage", "source": "source"}, "digest", child,
            "child-turn", [{"task_id": "task"}] * count, pending,
            lambda child: pytest.fail("rejected child cannot capture origin"))


def test_compatibility_exports_are_the_authoritative_objects():
    from sico.core import audit_questions as owner
    from sico.service import audit, audit_questions, audit_records
    for name in ("NEW_WINDOW_OPTION", "QUESTION", "bounded", "questions", "same_host_questions"):
        assert getattr(audit, name) is getattr(audit_questions, name) is getattr(owner, name)
    assert audit_records.native_record is audit.native_record is native_record


@pytest.mark.parametrize("child", [False, True])
@pytest.mark.parametrize("budget", ["pending", "task"])
def test_approval_budget_rejects_once_without_publishing_an_extra_record(running, child, budget):
    loop, _, sent = running
    loop.validate_audit_target = lambda: None
    if child:
        register(loop)
    params = {"threadId": "child", "turnId": "child-turn"} if child else {}
    count = 4 if budget == "pending" else 32
    for i in range(count):
        event = {**request(**params), "id": i + 100}
        assert loop._input_request(event, "turn")
        row = next(reversed(loop.audit.index.audits.values()))
        if budget == "task":
            answer(loop, row, "拒绝")
            assert loop.audit.deliver() == 1
    before = len(loop.audit.index.audits), len(sent)
    event = {**request(**params), "id": 999}
    loop._input_request(event, "turn")
    loop._input_request(event, "turn")
    assert len(loop.audit.index.audits) == before[0]
    assert len(sent) == before[1] + 1 and sent[-1]["error"]["code"] == -32602
    assert len(loop.audit.pending) == (4 if budget == "pending" else 0)
