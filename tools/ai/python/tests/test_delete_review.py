"""Host and raw-SKILL deletion reviews fail closed before any write."""

import json

import pytest
from sico.adapters.virtuoso import context_tools
from sico.core.contracts import BoundContext, ModelTurn, ToolCall, ToolResult
from sico.core.tools import Tool, no_arguments
from sico.providers.mock import ScriptedProvider
from sico.service.backend import create_backend
from sico.storage.journal import SessionJournal

from cadai.destructive_skill import APPROVAL as SKILL_APPROVAL
from cadai.destructive_skill import prepare as prepare_skill_review
from cadai.mcp_definitions import TOOLS_BY_NAME

DIGEST = "a" * 64


def review_data(kind="circuit_edit", digest=DIGEST):
    if kind == "circuit_edit":
        question = {
            "id": "circuit_edit",
            "header": "电路修改",
            "question": "将清空并重建审阅过的 cellview。",
            "options": [
                {"label": "执行这次修改", "description": "执行"},
                {"label": "取消", "description": "取消"},
            ],
        }
        labels = ("执行这次修改", "取消")
    else:
        question = {
            "id": "skill_deletion",
            "header": "删除审阅",
            "question": "将执行删除 SKILL。",
            "options": [
                {"label": SKILL_APPROVAL, "description": "执行"},
                {"label": "取消", "description": "取消"},
            ],
        }
        labels = (SKILL_APPROVAL, "取消")
    return {
        "review_required": True,
        "review_kind": kind,
        "review_digest": digest,
        "approval_label": labels[0],
        "rejection_label": labels[1],
        "selection_question": question,
        "review": {"target": "work/test/schematic"},
    }


class Broker:
    def get_context(self, context):
        return {"ok": True, "valid": True, "source_valid": True}

    def read(self, context, method, args=None):
        return {"ok": True, "valid": True, "source_valid": True}

    def circuit_call(self, context, request):
        raise AssertionError("Deletion review tests must not reach Virtuoso")


@pytest.fixture
def delete_backend(tmp_path):
    provider = ScriptedProvider([
        [ModelTurn("", (ToolCall("review-call", "fake_delete_review", {}),))],
        [ModelTurn("")],
    ])
    context = BoundContext("instance", "generation", "bound", {"cwd": str(tmp_path)})
    with SessionJournal(tmp_path, "delete_review") as journal:
        registry = context_tools(Broker(), journal)
        registry.register(Tool(
            "fake_delete_review",
            "Return one deletion review",
            {"type": "object", "properties": {}, "additionalProperties": False},
            no_arguments,
            lambda args, context: ToolResult(data=review_data()),
        ))
        backend = create_backend(provider, registry, journal, context)
        yield backend


def test_raw_skill_review_is_content_bound(tmp_path):
    path = tmp_path / "delete.il"
    path.write_text('(dbDeleteObject cv)\n', encoding="utf-8")
    first = prepare_skill_review("load_skill_file", {"path": "delete.il"}, tmp_path)
    assert first["review_kind"] == "skill_deletion"
    assert "dbDeleteObject" in first["review"]["calls"]
    assert first["selection_question"]["id"] == "skill_deletion"

    path.write_text('(dbDeleteObject cv) (dbDeletePropByName cv "x")\n', encoding="utf-8")
    changed = prepare_skill_review("load_skill_file", {"path": "delete.il"}, tmp_path)
    assert changed["review_digest"] != first["review_digest"]

    assert prepare_skill_review("eval_skill", {"code": "(get_context)"}, tmp_path) is None
    dynamic = prepare_skill_review(
        "eval_skill", {"code": '(eval "dbDeleteObject(cv)")'}, tmp_path
    )
    assert dynamic["review"]["calls"] == ["dynamic deletion text"]
    dispatch = prepare_skill_review("eval_skill", {"code": "(funcall f)"}, tmp_path)
    assert dispatch["review"]["calls"] == ["dynamic SKILL dispatch"]

    native_write = prepare_skill_review(
        "eval_skill", {"code": "(aiCreateExecute p d r)"}, tmp_path
    )
    assert "aiCreateExecute" in native_write["review"]["calls"]


@pytest.mark.parametrize(
    ("choice", "expected"),
    [("执行这次修改", "approved"), ("取消", "rejected")],
)
def test_destructive_review_needs_explicit_approval(delete_backend, choice, expected):
    backend = delete_backend
    stream = backend.run("Review this deletion", backend.cancelled)
    for event in stream:
        if event["kind"] == "model.status" and event["payload"]["phase"] == "waiting_user":
            break
    else:
        pytest.fail("Host did not open a blocking deletion review")
    before = len(backend.audit.index.audits)
    rows = [row for row in backend.audit.index.audits.values()
            if row.get("destructive_kind") == "circuit_edit"]
    assert len(rows) == 1 and rows[0]["status"] == "pending"
    row = rows[0]
    assert backend.audit.destructive_decision(
        "circuit_edit", DIGEST, backend.state.context
    ) == "pending"

    assert len(backend.audit.index.audits) == before

    backend.audit.answer(
        row["id"],
        {"circuit_edit": {"choice": choice, "text": ""}},
        "approve-delete",
        "user",
        row["task_id"],
    )
    backend.audit.deliver_local()
    assert backend.audit.destructive_decision(
        "circuit_edit", DIGEST, backend.state.context
    ) == expected
    assert not backend.audit.waiting_for_host()
    assert list(stream)[-1]["kind"] == "task.completed"


def test_circuit_execute_is_blocked_without_host_approval(delete_backend):
    backend = delete_backend
    backend._begin("Review this edit", backend.state.context, None, "chat")
    backend.active, backend.cancelled = True, lambda: False
    result = backend.tools.execute(
        ToolCall(
            "blocked-edit",
            "execute_circuit_edit",
            {
                "prepare_ref": "create-prepare:test",
                "geometry_plan_digest": "d" * 64,
                "request_id": "edit",
                "approval_digest": DIGEST,
            },
        ),
        backend.state.context,
    )
    assert result.status in {"approval_required", "waiting_user"}
    assert result.data["code"] in {
        "delete_review_required",
        "delete_review_pending",
    }
    assert result.data["operation_dispatched"] is False


def test_raw_skill_call_is_blocked_before_eval(delete_backend):
    backend = delete_backend
    backend._begin("Review this SKILL", backend.state.context, None, "chat")
    backend.active, backend.cancelled = True, lambda: False
    result = backend.tools.execute(
        ToolCall(
            "blocked-skill",
            "eval_skill",
            {"code": "(dbDeleteObject cv)"},
        ),
        backend.state.context,
    )
    assert result.status == "waiting_user"
    assert result.data["review_required"] is True
    assert result.data["review_kind"] == "skill_deletion"
    assert json.loads(json.dumps(result.data))["review"]["calls"] == ["dbDeleteObject"]


def test_only_destructive_write_tool_is_marked_destructive():
    assert TOOLS_BY_NAME["execute_circuit_edit"]["annotations"]["destructiveHint"] is True
    assert TOOLS_BY_NAME["prepare_circuit_edit"]["annotations"]["destructiveHint"] is False
