"""Evidence-bound user decisions through the pinned original Codex app-server."""

from __future__ import annotations

import json
import threading
import time

import pytest

from test_agent_codex_native import (  # noqa: F401
    CONTEXT,
    answer,
    gateway,
    native_codex,
    registry,
    settings,
)
from test_codex_child_input_runtime import call_named
from test_codex_resources_runtime import tool_call

from sico.service.backend import create_backend
from sico.storage.journal import SessionJournal


def _output(request, call_id):
    item = next(
        item
        for item in request["input"]
        if item.get("type") == "function_call_output" and item.get("call_id") == call_id
    )
    value = item["output"]
    if isinstance(value, list):
        value = "".join(part.get("text", "") for part in value)
    if value.startswith("Wall time:"):
        value = value.split("\nOutput:", 1)[-1]
    return json.loads(value)


def invoke(name, args, call_id, *, request=None):
    if request is None:
        return lambda request: invoke(name, args, call_id, request=request)
    namespace, separator, tool = name.rpartition("__")
    if separator:
        return tool_call(request, namespace, tool, call_id, args)
    return call_named(request, name, call_id, args)


@pytest.mark.parametrize("changed_description", [False, True])
def test_native_request_user_input_round_trip_and_dedup(
    tmp_path,
    native_codex,  # noqa: F811 -- imported pytest fixture
    gateway,  # noqa: F811 -- imported pytest fixture
    changed_description,
):
    def call(name, args, call_id, **kwargs):
        return invoke(name, args, call_id, **kwargs)

    def listed(request):
        return call("mcp__sico_ic__list_copilot_data", {}, "listing", request=request)

    def prepared(request):
        ids = [item["id"] for item in _output(request, "listing")["data"]["items"]]
        return call(
            "mcp__sico_ic__prepare_copilot_audit",
            {
                "request_id": "audit_request_1",
                "title": "仿真结果异常",
                "recommendation": "先复核已有结果",
                "rationale": "异常点需要明确后续范围。",
                "questions": [{
                    "id": "direction", "header": "推进方式", "question": "如何处理？",
                    "options": [
                        {"label": "复核现有结果", "description": "只读取已有数据。"},
                        {"label": "调整配置", "description": "建立新配置再评估。"},
                    ],
                }],
                "evidence_ids": ids,
            },
            "prepare",
            request=request,
        )

    def question(request):
        return call(
            "request_user_input",
            {"questions": [{
                "id": "direction", "header": "推进方式", "question": "如何处理？",
                "options": [
                    {"label": "复核现有结果", "description": "只读取已有数据。"},
                    {"label": "调整配置", "description": (
                        "创建新配置再评估。" if changed_description else "建立新配置再评估。"
                    )},
                ],
            }]},
            "question",
            request=request,
        )

    for response in (
        call("mcp__sico_ic__set_copilot_stage", {
            "work_title": "W", "stage_title": "S", "objective": "O", "acceptance": "A"
        }, "stage"),
        listed,
        prepared,
        question,
        answer("已按用户决定继续。"),
    ):
        gateway.responses.put(response)
    provider = settings(gateway, native_codex, "flat")
    with SessionJournal(tmp_path, "audit") as journal:
        backend = create_backend(provider, registry([]), journal, CONTEXT)
        backend.tool_profile.select("desktop_compat")
        result = []
        worker = threading.Thread(target=lambda: result.extend(backend.run("检查并询问")))
        worker.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not any(
            event["kind"] == "workbench.audit.opened" for event in journal.events()
        ):
            time.sleep(0.02)
        opened = next(
            event for event in journal.events() if event["kind"] == "workbench.audit.opened"
        )
        audit_id, task_id = opened["payload"]["id"], backend.state.task["id"]
        answers = {"direction": {"choice": "复核现有结果", "text": ""}}
        assert backend.audit.answer(audit_id, answers, "reply_1", "test-user", task_id)
        assert not backend.audit.answer(audit_id, answers, "reply_1", "test-user", task_id)
        worker.join(10)
        assert not worker.is_alive()
        assert result[-1]["kind"] == "task.completed", result
        kinds = [event["kind"] for event in journal.events()]
        assert kinds.count("workbench.audit.prepared") == 1
        assert kinds.count("workbench.audit.opened") == 1
        assert kinds.count("workbench.audit.reply_received") == 1
        assert kinds.count("workbench.audit.resumed") == 1
        row = next(iter(backend.audit.index.audits.values()))
        assert row["request_id"] == "audit_request_1" and row["status"] == "answered"
        assert row["recommendation"] == "先复核已有结果"
        assert row["questions"][0]["options"][1]["description"] == "建立新配置再评估。"
        assert "copilot-test-secret" not in json.dumps(journal.events())
        backend.close()
