"""Reads progress only with host effects, dependencies and a current question source."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from wait_reads_fixture import backend, host_question, invoke, native_question, project, start

from sico.core.read_dependencies import reference_dependencies
from sico.storage.journal import SessionJournal


@pytest.fixture
def waiting(tmp_path):
    project(tmp_path)
    with SessionJournal(tmp_path, "wait-reads") as journal:
        loop = backend(journal, tmp_path)
        start(loop)
        yield loop
        loop.runtime = None
        loop.close()


@pytest.mark.parametrize("kind,allowed,blocked", [
    ("pdk", ["independent", "target"], ["pdk", "model", "parameters", "decision", "undeclared"]),
    # An existing-design decision gates writing to that target, not reading it:
    # only reads that depend on the decision itself still wait.
    ("target", ["independent", "pdk", "model", "target", "parameters"],
     ["decision", "undeclared"]),
    ("native", ["independent"], ["pdk", "model", "parameters", "target", "decision", "undeclared"]),
])
def test_declared_reads_follow_question_dependencies(waiting, kind, allowed, blocked):
    row = native_question(waiting) if kind == "native" else host_question(waiting, kind)
    task = waiting.state.task["id"]
    for name in allowed:
        result = invoke(waiting, "read_project_document", {"path": "docs/" + name + ".txt"})
        assert result["status"] == "ok" and result["data"]["text"] == "Evidence for " + name
    for name in blocked:
        before = len(waiting.journal.events())
        result = invoke(waiting, "read_project_document", {"path": "docs/" + name + ".txt"})
        assert result["status"] == "waiting_user" and result["data"]["automatic_retry"] is False
        assert len(waiting.journal.events()) == before
    assert row["status"] == "pending" and list(waiting.audit.pending) == [row["id"]]
    assert waiting.state.task["id"] == task
    assert waiting.state.task["activity"]["phase"] == "waiting_user"
    assert not waiting.state.task["pending_calls"]


@pytest.mark.parametrize("name,args", [
    ("get_context", {}), ("get_pdk_preparation", {}),
    ("list_pdk_devices", {}), ("read_circuit_spec", {"path": "docs/independent.txt"}),
    ("eval_skill", {"code": "1"}), ("get_binding_operations", {}),
    ("list_project_documents", {"path": "docs"}),
    ("search_project_documents", {"pattern": "Evidence", "path": "docs"}),
    ("Search", {"pattern": "Evidence", "path": "docs"}),
    ("cds_help", {"action": "discover", "install_root": "/missing"}),
    ("unregistered_read", {"readOnlyHint": True}),
])
def test_no_hint_domain_or_model_claim_can_bypass_wait(waiting, name, args):
    host_question(waiting)
    assert invoke(waiting, name, args)["status"] == "waiting_user"


@pytest.mark.parametrize("change", ["effect", "dependencies", "hint", "destructive"])
def test_adapter_contract_is_required_in_addition_to_hint(waiting, change):
    host_question(waiting)
    tool = waiting.tools.resolve("read_project_document")
    changed = {
        "effect": {"effect": "unknown"}, "dependencies": {"input_dependencies": None},
        "hint": {"annotations": {}},
        "destructive": {"annotations": {"readOnlyHint": True, "destructiveHint": True}},
    }[change]
    waiting.tools._tools[tool.name] = replace(tool, **changed)
    assert invoke(waiting, tool.name, {"path": "docs/independent.txt"})["status"] == "waiting_user"


def test_execution_domain_is_not_part_of_the_read_contract(waiting):
    # The domain says where a tool runs, not what it changes: a declared read in
    # a live session domain (for example design evidence read through the
    # broker) stays admitted while a question is pending.
    host_question(waiting)
    tool = waiting.tools.resolve("read_project_document")
    waiting.tools._tools[tool.name] = replace(tool, execution_domain="virtuoso")
    assert invoke(waiting, tool.name, {"path": "docs/independent.txt"})["status"] == "ok"


def test_real_search_and_reference_adapters_work_while_waiting(
    waiting, reference_root, monkeypatch,
):
    monkeypatch.setenv("SICO_AI_SKILL_REFERENCE_ROOT", str(reference_root))
    host_question(waiting)
    result = invoke(waiting, "Search", {"pattern": "Evidence", "path": "docs/independent.txt"})
    assert result["status"] == "ok" and result["data"]["returned"] == 1
    result = invoke(waiting, "search_skill_api", {"query": "substring", "limit": 1})
    assert result["status"] == "ok" and "substring" in str(result["data"])
    assert waiting.tools.resolve("get_skill_api").input_dependencies is reference_dependencies
    assert "effect" not in waiting.tools.schemas()[0]


def test_local_help_and_skill_check_stay_readable_while_waiting(waiting):
    """本地帮助与语法检查不依赖任何待决事项；待答复期间必须仍可读取。"""
    host_question(waiting)
    for name, args in (
        ("mcp_help", {"action": "list", "entity": "groups"}),
        ("check_skill", {"code": "(substring \"abc\" 1 2)"}),
    ):
        tool = waiting.tools.resolve(name)
        assert tool is not None and tool.effect == "read", name
        assert tool.input_dependencies is reference_dependencies, name
        assert invoke(waiting, name, args)["status"] == "ok", name


@pytest.mark.parametrize("boundary", ["task", "context", "evidence", "status", "rpc",
                                      "thread"])
def test_stale_host_question_cannot_authorize_a_read(waiting, boundary):
    row = host_question(waiting)
    if boundary == "task":
        row["task_id"] = "other"
    elif boundary == "context":
        waiting.state.context.snapshot["changed"] = True
    elif boundary == "evidence":
        waiting.audit.index.data[row["evidence"][0]["id"]]["digest"] = "changed"
    elif boundary == "status":
        row["status"] = "invalidated"
    elif boundary == "rpc":
        waiting.runtime.rpc = SimpleNamespace(send=lambda message, **kwargs: None)
    elif boundary == "thread":
        from codex_thread_fixture import bind_thread
        bind_thread(waiting, "changed")
    assert invoke(waiting, "read_project_document", {"path": "docs/independent.txt"})[
        "status"] == "waiting_user"


@pytest.mark.parametrize("blocking", [True, False])
def test_bound_host_question_and_received_answer_still_hold_dependencies(waiting, blocking):
    row = host_question(waiting)
    native_question(waiting, questions=row["questions"], blocking=blocking)
    assert not waiting.audit.waiting_for_host()
    assert invoke(waiting, "read_project_document", {"path": "docs/independent.txt"})[
        "status"] == "ok"
    assert invoke(waiting, "get_context")["status"] == "waiting_user"
    waiting.audit.answer(row["id"], {"pdk_library": {"choice": "first", "text": ""}},
                         "reply", "user", row["task_id"])
    assert invoke(waiting, "get_context")["status"] == "waiting_user"
    assert waiting.audit.deliver() == 1
    assert invoke(waiting, "get_context")["status"] == "ok"


def test_unknown_question_adds_dependency_and_alias_preserves_policy(waiting):
    host_question(waiting)
    waiting.tools.alias("document_alias", "read_project_document")
    assert invoke(waiting, "document_alias", {"path": "docs/target.txt"})["status"] == "ok"
    native_question(waiting)
    assert invoke(waiting, "document_alias", {"path": "docs/target.txt"})[
        "status"] == "waiting_user"
    assert invoke(waiting, "document_alias", {"path": "docs/independent.txt"})["status"] == "ok"


def test_supplementary_question_does_not_gate_dependent_reads(waiting):
    waiting.workbench.stage(
        {"work_title": "W", "stage_title": "S", "objective": "O", "acceptance": "A"},
        waiting.state.context,
    )
    assert invoke(waiting, "read_project_document",
                  {"path": "docs/independent.txt"})["status"] == "ok"
    evidence = next(reversed(waiting.workbench.index.data))
    prepared = waiting.audit.prepare({
        "request_id": "preference", "title": "Preference",
        "recommendation": "Use the default", "rationale": "Optional presentation choice",
        "evidence_ids": [evidence], "review_class": "supplementary",
        "questions": [{"id": "preference", "header": "Preference",
                       "question": "Which label?", "options": []}],
    }, waiting.state.context).data
    waiting.audit.open({"id": 71, "params": {
        "threadId": "thread", "turnId": "turn", "itemId": "item71",
        "isBlocking": False, "questions": prepared["questions"],
    }})
    row = waiting.audit.index.audits[prepared["audit_id"]]
    assert row["review_class"] == "supplementary"
    assert waiting.state.task["supplementary_audits"] == [row["id"]]
    assert "waiting_audits" not in waiting.state.task
    assert not waiting.audit.waiting_for_input()
    assert invoke(waiting, "read_project_document", {"path": "docs/pdk.txt"})["status"] == "ok"
    assert waiting.audit.answer_supplementary() == 1
    assert row["status"] == "answered"


def test_declared_file_restrictions_do_not_change_ordinary_reads(waiting):
    assert invoke(waiting, "read_project_document", {"path": "docs/undeclared.txt"})[
        "status"] == "ok"
    assert invoke(waiting, "read_project_document", {"path": "../outside"})[
        "status"] == "tool_error"


@pytest.mark.parametrize("boundary", ["thread", "turn", "connection"])
def test_root_native_question_checks_original_thread_turn_connection(waiting, boundary):
    native_question(waiting)
    if boundary == "thread":
        from codex_thread_fixture import bind_thread
        bind_thread(waiting, "other")
    elif boundary == "turn":
        waiting.steering.turn_id = "ended"
    else:
        waiting.runtime.rpc = SimpleNamespace(send=lambda message, **kwargs: None)
    assert invoke(waiting, "read_project_document", {"path": "docs/independent.txt"})[
        "status"] == "waiting_user"
