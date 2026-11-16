"""Original Codex 0.154 uses the Copilot application tools through actual stdio MCP."""

from __future__ import annotations

import json

import pytest
from test_agent_codex_native import (  # noqa: F401
    CONTEXT,
    answer,
    gateway,
    native_codex,
    registry,
    settings,
    sse,
)
from test_agent_workbench import STAGE

from sico.service.backend import create_backend
from sico.storage.journal import SessionJournal
from sico.storage.workbench import WorkbenchIndex


def tool_output(request, call_id):
    output = next(
        i["output"]
        for i in request["input"]
        if i.get("type") == "function_call_output" and i.get("call_id") == call_id
    )
    if isinstance(output, list):
        output = "".join(block.get("text", "") for block in output)
    if output.startswith("Wall time:"):
        _, separator, output = output.partition("\nOutput:")
        assert separator
    value = json.loads(output)
    assert value["status"] == "ok", value
    return value["data"]


@pytest.mark.parametrize("tool_format", ["flat", "native", "switch"])
def test_codex_publishes_evidence_report_and_reuses_stage_after_resume(
    tmp_path,
    native_codex,  # noqa: F811 -- imported pytest fixture
    gateway,  # noqa: F811 -- imported pytest fixture
    tool_format,
):
    observed = {}

    def invoke(name, args, call_id):
        identity = (
            {"namespace": "mcp__sico_ic", "name": name}
            if tool_format == "native"
            else {"name": "mcp__sico_ic__" + name}
        )
        return sse(
            {
                "type": "function_call",
                "id": "fc_" + call_id,
                "call_id": call_id,
                "arguments": json.dumps(args),
                **identity,
            }
        )

    def listed(request):
        observed["stage"] = tool_output(request, "stage")
        return invoke("list_copilot_data", {}, "listing")

    def publish(request):
        data = tool_output(request, "listing")
        observed["evidence"] = [r["id"] for r in data["items"]]
        assert len(observed["evidence"]) >= 3
        return invoke(
            "publish_copilot_report",
            {
                "title": "只读上下文汇报",
                "markdown": "## 当前来源\n\n已读取绑定窗口的上下文。",
                "outcome": "completed",
                "next_steps": "尚无仿真结果，不能判定设计通过。",
                "evidence_ids": observed["evidence"],
            },
            "publication",
        )

    def finished(request):
        observed["report"] = tool_output(request, "publication")
        return answer("已发布[上下文汇报](" + observed["report"]["url"] + ")。")

    def resumed(request):
        stage = observed["stage"]
        return invoke(
            "set_copilot_stage",
            {
                **STAGE,
                "work_id": stage["work"]["id"],
                "stage_id": stage["stage"]["id"],
            },
            "continued_stage",
        )

    def continued(request):
        stage = tool_output(request, "continued_stage")["stage"]
        assert stage["id"] == observed["stage"]["stage"]["id"]
        return answer("继续使用相同工程任务与阶段。")

    for response in (
        invoke("set_copilot_stage", STAGE, "stage"),
        listed,
        publish,
        finished,
        resumed,
        continued,
    ):
        gateway.responses.put(response)
    provider = settings(gateway, native_codex, tool_format)
    with SessionJournal(tmp_path, "copilot_mcp") as journal:
        backend = create_backend(provider, registry([]), journal, CONTEXT)
        try:
            events = list(backend.run("读取上下文并发布汇报"))
            assert events[-1]["kind"] == "task.completed", events
            published = [e for e in events if e["kind"] == "workbench.report.published"]
            assert len(published) == 1
            evidence = published[0]["payload"]["evidence"]
            assert {r["id"] for r in evidence} == set(observed["evidence"])
            assert "copilot-test-secret" not in json.dumps(journal.events())
            thread = backend.thread_id
        finally:
            backend.close()
    with SessionJournal(tmp_path, "copilot_mcp") as journal:
        backend = create_backend(provider, registry([]), journal, CONTEXT)
        try:
            events = list(backend.run("继续这一阶段"))
            assert events[-1]["kind"] == "task.completed", events
            assert backend.thread_id == thread
            index = WorkbenchIndex(journal).sync()
            assert len(index.works) == len(index.stages) == 1
            assert len([r for r in index.reports.values() if r["kind"] == "published"]) == 1
            assert len(gateway.requests) == 6
        finally:
            backend.close()
