"""Execution scope snapshots and task-bound one-shot native approval decisions."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_codex_elicitation import running, resolved  # noqa: F401
from test_codex_child_input import register
from test_codex_turn_inputs import backend

from sico.codex.access_policy import default_access, turn_params
from sico.codex.turn_options import TurnOptions
from sico.service.controller import SessionController
from sico.storage.journal import SessionJournal


def request(kind="commandExecution", **params):
    return {"id": 17, "method": "item/" + kind + "/requestApproval", "params": {
        "threadId": "thread", "turnId": "turn", "itemId": "operation", "startedAtMs": 0,
        "command": "touch /outside/project", "cwd": "/project", "reason": "写入项目外文件",
        **params}}


def answer(loop, row, choice):
    loop.audit.answer(row["id"], {"codex_approval": {"choice": choice, "text": ""}},
                      "reply", "user", row["task_id"])


@pytest.mark.parametrize("kind", ["commandExecution", "fileChange", "permissions"])
@pytest.mark.parametrize("choice", ["仅批准本次", "拒绝"])
def test_native_decisions_only_follow_explicit_user_answer(running, kind, choice):
    loop, controller, sent = running
    loop.validate_audit_target = lambda: None
    changes = [{"path": "/outside/project", "kind": {"type": "add"}, "diff": "+hello"}]
    loop.native.pending[("thread", "turn", "operation")] = {"input": {"changes": changes}}
    permissions = {"fileSystem": {"write": ["/outside/project"]}}
    event = request(kind, permissions=permissions)
    assert loop._input_request(event, "turn")
    assert not sent
    row = next(reversed(loop.audit.index.audits.values()))
    assert "/outside/project" in row["rationale"]
    loop._input_request(event, "turn")
    assert len(loop.audit.pending) == 1
    answer(loop, row, "批准本回合权限" if kind == "permissions" and choice == "仅批准本次" else choice)
    assert not sent
    assert loop.audit.deliver() == 1
    result = sent[0]["result"]
    if kind == "permissions":
        assert result["scope"] == "turn"
        assert result["permissions"] == (permissions if choice == "仅批准本次" else {
            "fileSystem": {"entries": []}, "network": {"enabled": False}})
    else:
        assert result == {"decision": "accept" if choice == "仅批准本次" else "decline"}
    loop._input_request(event, "turn")
    assert len(sent) == 1 and not loop.audit.pending
    assert not controller.inbox.pending


def test_command_approval_records_the_display_snippet(running):
    """命令审批记录命令原文与执行环境，供卡片按代码块呈现。"""
    loop, _controller, _sent = running
    loop.validate_audit_target = lambda: None
    event = request("commandExecution", environmentId="local")
    assert loop._input_request(event, "turn")
    row = next(reversed(loop.audit.index.audits.values()))
    approval = row["native_approval"]
    assert approval["method"] == "item/commandExecution/requestApproval"
    assert approval["command"] == "touch /outside/project"
    assert approval["environment"] == "local"
    assert approval["cwd"] == "/project"


def test_other_approvals_have_no_command_snippet(running):
    """文件变更审批不带命令片段：卡片照旧只显示 JSON 依据。"""
    loop, _controller, _sent = running
    loop.validate_audit_target = lambda: None
    changes = [{"path": "/outside/project", "kind": {"type": "add"}, "diff": "+hello"}]
    loop.native.pending[("thread", "turn", "operation")] = {"input": {"changes": changes}}
    assert loop._input_request(request("fileChange"), "turn")
    row = next(reversed(loop.audit.index.audits.values()))
    assert "command" not in row["native_approval"]


def test_answer_refreshes_published_task_without_another_worker_event(running):
    """审阅答复属于命令/RPC 线程，事件批次也要发布新的任务快照。"""
    loop, controller, _sent = running
    loop.validate_audit_target = lambda: None
    assert loop._input_request(request("commandExecution", environmentId="local"), "turn")
    row = next(reversed(loop.audit.index.audits.values()))

    first = controller.updates.read_updates()
    task = first["snapshot"]["task"]
    assert task["waiting_audits"] == [row["id"]]
    assert task["activity"]["phase"] == "waiting_user"

    answer(loop, row, "仅批准本次")
    assert loop.audit.deliver() == 1
    second = controller.updates.read_updates(after=first["committed_sequence"])
    task = second["snapshot"]["task"]
    assert "waiting_audits" not in task
    assert task["activity"]["phase"] == "waiting_model"


def test_approval_detail_carries_the_command_code_block(running):
    """归档详情把命令作为代码块给出，与审阅卡片共用同一渲染通道。"""
    from sico.service.detail_content import DetailContent

    loop, _controller, _sent = running
    loop.validate_audit_target = lambda: None
    event = request("commandExecution", environmentId="local")
    assert loop._input_request(event, "turn")
    row = next(reversed(loop.audit.index.audits.values()))
    detail = DetailContent(loop.audit.index).prepare("audit", row["id"])
    assert detail["markdown"].startswith("```") and detail["markdown"].rstrip().endswith("```")
    assert "touch /outside/project" in detail["markdown"]
    assert "命令（local · /project）：" in detail["prefix"]
    assert "依据" in detail["suffix"]


@pytest.mark.parametrize("boundary", ["resolved", "connection", "turn", "task", "cancel"])
def test_old_approval_cannot_authorize_new_work(running, boundary):
    loop, _, sent = running
    loop.validate_audit_target = lambda: None
    loop._input_request(request(), "turn")
    row = next(reversed(loop.audit.index.audits.values()))
    answer(loop, row, "仅批准本次")
    if boundary == "resolved":
        loop.audit.requests.resolved(resolved(request_id=17))
    elif boundary == "connection":
        loop.runtime = SimpleNamespace(rpc=SimpleNamespace(send=lambda message, **kwargs: sent.append(message)))
    elif boundary == "turn":
        loop.steering.end()
    elif boundary == "task":
        loop.state.task["id"] = "different"
    else:
        loop.cancelled = lambda: True
    loop.audit.deliver()
    assert not any(item.get("result", {}).get("decision") == "accept" for item in sent)


def test_never_policy_declines_without_opening_audit(running):
    loop, _, sent = running
    loop.state.task["turn_options"] = {"access": {**default_access(), "approval": "never"}}
    loop._input_request(request(), "turn")
    assert sent == [{"id": 17, "result": {"decision": "decline"}}]
    assert not loop.audit.pending


def test_child_approval_is_bound_to_registered_child(running):
    loop, _, sent = running
    loop.validate_audit_target = lambda: None
    register(loop)
    loop._input_request(request(threadId="child", turnId="child-turn"), "turn")
    row = next(reversed(loop.audit.index.audits.values()))
    assert row["child_thread_id"] == "child"
    answer(loop, row, "拒绝")
    assert loop.audit.deliver() == 1
    assert sent[-1]["result"] == {"decision": "decline"}


def test_free_text_cannot_grant_permission(running):
    loop, _, _ = running
    loop._input_request(request(), "turn")
    row = next(reversed(loop.audit.index.audits.values()))
    with pytest.raises(ValueError, match="明确选择"):
        loop.audit.answer(row["id"], {"codex_approval": {"choice": "", "text": "yes"}},
                          "reply", "user", row["task_id"])


def test_queue_freezes_scope_without_publishing_unacknowledged_escalation(tmp_path):
    with SessionJournal(tmp_path, "access") as journal:
        loop = backend(journal)
        controller = SessionController(loop)
        controller.paused = True
        access = {"sandbox": "danger-full-access", "approval": "never", "network": True}
        controller.submit("explicit full access", turn_options={"access": access})
        access["sandbox"] = "read-only"
        controller.submit("default scope")
        first, second = [r["message"]["turn_options"] for r in controller.inbox.pending]
        assert first["access"]["sandbox"] == "danger-full-access"
        assert second["access"] == loop.turn_options.selected["access"] == default_access()
        loop._event("codex.turn.settings", {"status": "rejected", "options": first})
    with SessionJournal(tmp_path, "access") as journal:
        assert backend(journal).turn_options.selected["access"] == default_access()


def test_legacy_restore_and_accepted_scope_have_one_authority():
    loop = SimpleNamespace(provider=SimpleNamespace(model="test-model"))
    records = [{"kind": "codex.thread", "payload": {}}]
    assert TurnOptions(loop, records).selected["access"] == default_access(legacy=True)
    chosen = {"model": "test-model", "mode": "default", "serviceTier": "default",
              "access": {**default_access(), "sandbox": "read-only"}}
    records.append({"kind": "codex.turn.settings", "payload": {
        "status": "accepted", "options": deepcopy(chosen)}})
    assert TurnOptions(loop, records).selected == chosen
    for mode, wire in [("read-only", "readOnly"), ("workspace-write", "workspaceWrite"),
                       ("danger-full-access", "dangerFullAccess")]:
        params = turn_params({**default_access(), "sandbox": mode}, interactive=True)
        assert params["sandboxPolicy"]["type"] == wire
        assert params["approvalPolicy"]["granular"]["sandbox_approval"] is True
        assert params["approvalsReviewer"] == "user"
