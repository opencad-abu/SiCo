"""Engineering associations and immutable evidence, using offline shared result archives."""

from __future__ import annotations

import json
import uuid
from copy import deepcopy
from pathlib import Path

import pytest
from test_result_contract import fixture_data

from cadai.result_tools import ResultStore
from sico.adapters.virtuoso import context_tools
from sico.core.contracts import BoundContext, ModelTurn, TextDelta, ToolCall, ToolResult
from sico.core.tools import tool_message
from sico.providers.mock import ScriptedProvider
from sico.service.backend import create_backend
from sico.service.controller import SessionController
from sico.core.session_events import EventCursor
from sico.storage.history import SessionReader
from sico.storage.journal import SessionJournal
from sico.storage.workbench import LOCAL_TOOLS, WorkbenchIndex, object_link, parse_link

STAGE = {
    "work_title": "放大器测量复核",
    "stage_title": "已有结果检查",
    "objective": "核对已有 History 的点结果",
    "acceptance": "保留全部坐标、单位和缺失项",
}


def backend_for(journal, provider=None):
    context = BoundContext(
        "instance",
        "generation",
        "source",
        {
            "cwd": str(journal.root.parents[2]),
            "cellview": {
                "lib": "AnalogWork", "cell": "amplifier", "view_name": "maestro",
                "oa_view_type": "maestro",
                "oa_view_type_source": "platform_api",
            },
        },
    )

    class NoHost:
        def get_context(self, bound):
            return bound.snapshot

        def circuit_call(self, *args):
            pytest.fail("This workbench acceptance must not call Virtuoso")

    tools = context_tools(NoHost(), journal)
    backend = create_backend(provider or ScriptedProvider([]), tools, journal, context)
    return backend


def begin(backend, text="复核归档结果"):
    backend._begin(text, None, None)
    backend._event("task.started", {"text": text, "context": backend.state.context.record()})


def call(backend, name, args, *, result=None):
    invocation = ToolCall(uuid.uuid4().hex, name, args)
    backend._event("tool.started", invocation.record())
    reply = result or backend.tools.execute(invocation, backend.state.context)
    message = tool_message(
        invocation, reply, backend.config.tool_result_chars, backend.journal.artifact
    )
    backend._event(
        "tool.finished",
        {
            "id": invocation.id,
            "name": name,
            "result": json.loads(message["content"]),
        },
    )
    backend.workbench.index.sync()
    return reply


def publish(backend, ids, **extra):
    return call(
        backend,
        "publish_copilot_report",
        {
            "title": "归档测量复核",
            "markdown": "## 结果\n\n已复核归档，存在不满足规格的点。",
            "outcome": "completed",
            "next_steps": "等待确定后续调整范围。",
            "evidence_ids": ids,
            **extra,
        },
    )


def archived_result(backend):
    _, _, snapshot, contract = fixture_data()
    store = ResultStore(backend.journal.root.parents[2])
    ref, artifact = store.put("results", snapshot)
    call(
        backend,
        "read_maestro_results",
        {"context_ref": "fixture", "history": snapshot["history"]},
        result=ToolResult(data={"result_ref": ref, "artifact": artifact}),
    )
    source = list(backend.workbench.index.data.values())[-1]
    result = call(backend, "evaluate_measurement_specs", {"result_ref": ref, "contract": contract})
    assert result.status == "ok", result
    measured = list(backend.workbench.index.data.values())[-1]
    return source, measured, result.data


def test_shared_tools_stage_association_revision_and_read_only_replay(tmp_path):
    with SessionJournal(tmp_path, "engineering") as journal:
        backend = backend_for(journal)
        assert LOCAL_TOOLS <= {t["name"] for t in backend.tools.schemas()}
        assert not LOCAL_TOOLS.intersection(t["name"] for t in context_tools(None).schemas())
        begin(backend)
        stage = call(backend, "set_copilot_stage", STAGE).data
        index = backend.workbench.index
        source, measured, result = archived_result(backend)
        assert measured["category"] == "process" and source["category"] == "source"
        assert source["id"] in measured["parents"]
        listed = call(backend, "list_copilot_data", {}).data["items"]
        registered = next(r for r in listed if r["id"] == measured["id"])
        assert registered["result_refs"] == [result["report_ref"]]
        assert registered["input_refs"] == source["result_refs"]
        assert source["requested"]["history"] == "CustomHistory.9"
        assert result["spec_qualified"] is False
        first = publish(backend, [source["id"], measured["id"]])
        assert first.status == "ok", first
        old = deepcopy(index.resolve(*parse_link(first.data["url"], journal.session_id)))
        assert old["outcome"] == "completed" and result["qualification"] == "fail"
        assert index.stages[old["stage_id"]]["status"] == "in_progress"
        backend._event("model.completed", {"text": "已发布复核报告。"})
        backend._finish("completed")
        assert len(index.sync().report_list()) == 1
        backend = backend_for(journal)
        begin(backend, "补充限制说明")
        args = {**STAGE, "work_id": stage["work"]["id"], "stage_id": stage["stage"]["id"]}
        assert call(backend, "set_copilot_stage", args).status == "ok"
        revision = publish(
            backend,
            [source["id"], measured["id"]],
            report_id=first.data["report_id"],
            markdown="补充：尚未读取波形。",
        )
        assert revision.status == "ok" and revision.data["version"] == 2
        duplicate = publish(
            backend,
            [source["id"], measured["id"]],
            report_id=first.data["report_id"],
            markdown="补充：尚未读取波形。",
        )
        assert duplicate.data == revision.data
        assert backend.workbench.index.reports[old["key"]] == old
        backend._finish("completed")
        before = {p: p.stat().st_mtime_ns for p in journal.root.rglob("*")}
        offline = WorkbenchIndex(SessionReader(journal.root, journal.session_id)).sync()
        offline.sync()
        assert len(offline.report_list()) == 2
        assert len(offline.material(measured["id"])["rows"]) == 2
        assert before == {p: p.stat().st_mtime_ns for p in journal.root.rglob("*")}


def test_published_report_freezes_cad_object_references(tmp_path):
    with SessionJournal(tmp_path, "object-ref-report") as journal:
        backend = backend_for(journal)
        begin(backend)
        call(backend, "set_copilot_stage", STAGE)
        source, _measured, _result = archived_result(backend)
        response = publish(
            backend,
            [source["id"]],
            markdown="检查 inst:/NM1 和 cv:/AnalogWork/amplifier/maestro。",
        )
        assert response.status == "ok", response
        kind, key = parse_link(response.data["url"], journal.session_id)
        report = backend.workbench.index.resolve(kind, key)
        assert [(row["kind"], row["name"]) for row in report["object_refs"]] == [
            ("instance", "NM1"),
            ("cellview", "AnalogWork/amplifier/maestro"),
        ]


def test_stage_switch_keeps_old_evidence_and_interactions_in_original_stage(tmp_path):
    with SessionJournal(tmp_path, "stages") as journal:
        backend = backend_for(journal)
        begin(backend)
        first = call(backend, "set_copilot_stage", STAGE).data
        source, measured, _ = archived_result(backend)
        audit = backend._event("model.completed", {"text": "点结果已核对。"})
        second = call(
            backend,
            "set_copilot_stage",
            {
                **STAGE,
                "stage_title": "限制整理",
                "work_id": first["work"]["id"],
            },
        ).data
        call(backend, "get_context", {})
        index = backend.workbench.index
        assert source["stage_id"] == measured["stage_id"] == first["stage"]["id"]
        assert list(index.data.values())[-1]["stage_id"] == second["stage"]["id"]
        associated = index.association(audit["task_id"], audit["sequence"])
        assert associated["stage_id"] == first["stage"]["id"]
        assert call(backend, "set_copilot_stage", STAGE).status == "tool_error"
        assert publish(backend, [source["id"]]).status == "ok"
        listed = call(
            backend,
            "list_copilot_data",
            {
                "work_id": first["work"]["id"],
                "stage_id": first["stage"]["id"],
                "limit": 1,
            },
        ).data
        assert len(listed["items"]) == 1 and listed["next_offset"] == 1
        assert len(listed["works"]) == len(listed["stages"]) == 1
        assert listed["identity_next_offset"] == 1


def test_readable_work_and_stage_ids_create_new_identity(tmp_path):
    with SessionJournal(tmp_path, "readable-ids") as journal:
        backend = backend_for(journal)
        begin(backend, "开始采集pdk数据smic18mmrf")
        args = {
            "work_id": "pdk_data_smic18mmrf",
            "work_title": "smic18mmrf PDK 数据采集",
            "stage_id": "collect_standard_data",
            "stage_title": "采集并形成可用 PDK 数据",
            "objective": "采集目录、参数、端口和仿真接口",
            "acceptance": "采集流程返回 ready 并保留缺项",
        }
        first = call(backend, "set_copilot_stage", args)
        assert first.status == "ok"
        assert first.data["work"] == {
            "id": "pdk_data_smic18mmrf", "title": "smic18mmrf PDK 数据采集"}
        assert first.data["stage"]["id"] == "collect_standard_data"

        repeated = call(backend, "set_copilot_stage", args)
        assert repeated.status == "ok"
        assert len(backend.workbench.index.works) == 1
        assert len(backend.workbench.index.stages) == 1

        conflict = call(
            backend, "set_copilot_stage", {**args, "work_title": "另一个工作"})
        assert conflict.status == "tool_error"
        assert "already has title" in conflict.summary


def test_publication_rejects_unknown_cross_work_and_changed_criteria(tmp_path):
    with SessionJournal(tmp_path, "validation") as journal:
        backend = backend_for(journal)
        assert call(backend, "list_copilot_data", {}).status == "tool_error"
        begin(backend)
        assert publish(backend, ["d_1_input"]).status == "tool_error"
        first = call(backend, "set_copilot_stage", STAGE).data
        source = list(backend.workbench.index.data)[0]
        report = publish(backend, [source])
        assert report.status == "ok"
        backend._finish("completed")
        begin(backend, "独立任务")
        call(backend, "set_copilot_stage", {**STAGE, "work_title": "独立任务"})
        for ids in ([source], ["not_recorded"], []):
            response = publish(backend, ids)
            assert response.status in {"tool_error", "invalid_arguments"}
        assert len(backend.workbench.index.reports) == 1
        backend._finish("completed")
        begin(backend)
        changed = {
            **STAGE,
            "work_id": first["work"]["id"],
            "stage_id": first["stage"]["id"],
            "acceptance": "changed",
        }
        response = call(backend, "set_copilot_stage", changed)
        assert response.status == "tool_error" and "criteria" in response.summary


@pytest.mark.parametrize("mode", ["changed", "missing", "foreign", "symlink"])
def test_evidence_validation_retains_original_report(tmp_path, mode):
    with SessionJournal(tmp_path, "integrity") as journal:
        backend = backend_for(journal)
        begin(backend)
        call(backend, "set_copilot_stage", STAGE)
        _, measured, result = archived_result(backend)
        report = publish(backend, [measured["id"]])
        index = backend.workbench.index
        published = deepcopy(index.report_list()[0])
        path = Path(result["artifact"]["path"])
        if mode == "changed":
            path.write_text("{}")
        elif mode == "missing":
            path.unlink()
        elif mode == "symlink":
            original = path.read_bytes()
            path.unlink()
            other = tmp_path / "other.json"
            other.write_bytes(original)
            path.symlink_to(other)
        else:
            foreign = deepcopy(result)
            foreign["artifact"]["path"] = str(tmp_path / path.name)
            call(backend, "read_maestro_results", {}, result=ToolResult(data=foreign))
            measured = list(index.data.values())[-1]
        response = publish(backend, [measured["id"]], report_id=report.data["report_id"])
        assert response.status == "tool_error" and "checksum" in response.summary
        assert index.report_list() == [published]


def test_truncated_evidence_failure_and_reference_provenance(tmp_path):
    with SessionJournal(tmp_path, "provenance") as journal:
        backend = backend_for(journal)
        begin(backend)
        call(backend, "set_copilot_stage", STAGE)
        call(
            backend,
            "read_maestro_results",
            {},
            result=ToolResult(
                data={
                    "result_ref": "results_fixture",
                    "large": "x" * 15000,
                }
            ),
        )
        index = backend.workbench.index
        source = list(index.data.values())[-1]
        assert source["value"]["truncated"]
        call(
            backend,
            "query_maestro_results",
            {"result_ref": "results_fixture"},
            result=ToolResult("tool_error", "missing", {"result_ref": "results_fixture"}),
        )
        failed = list(index.data.values())[-1]
        assert failed["status"] == "tool_error" and source["id"] in failed["parents"]
        # A repeated returned reference alone is not evidence of a dependency.
        call(
            backend,
            "read_maestro_results",
            {},
            result=ToolResult(
                data={
                    "result_ref": "results_fixture",
                }
            ),
        )
        assert source["id"] not in list(index.data.values())[-1]["parents"]
        artifact = source["value"]["artifact"]
        (journal.root / artifact["path"]).write_text("{}")
        assert publish(backend, [source["id"]]).status == "tool_error"


def test_plain_model_reply_is_only_execution_record(tmp_path):
    provider = ScriptedProvider([[TextDelta("已完成查询。"), ModelTurn("已完成查询。")]])
    with SessionJournal(tmp_path, "reply") as journal:
        backend = backend_for(journal, provider)
        events = list(backend.run("普通查询"))
        assert events[-1]["kind"] == "task.completed"
        index = backend.workbench.index.sync()
        assert [r["kind"] for r in index.report_list()] == ["execution"]
        assert not any(e["kind"] == "workbench.report.published" for e in events)
        assert "publish_copilot_report" in provider.requests[0]["system"]


def test_source_arguments_are_part_of_evidence_and_private_archive_is_not_indexed(tmp_path):
    with SessionJournal(tmp_path, "conditions") as journal:
        backend = backend_for(journal)
        begin(backend)
        call(backend, "set_copilot_stage", STAGE)
        source, _, _ = archived_result(backend)
        index = backend.workbench.index
        source["inputs"]["history"] = "different_history"
        assert publish(backend, [source["id"]]).status == "tool_error"
        before = len(index.data)
        artifact = journal.artifact(b'[{"provider_blocks":[{"private":"hidden"}]}]')
        backend._event("context.compacted", {"artifact": artifact})
        assert len(index.sync().data) == before
        assert "provider_blocks" not in json.dumps(index.data)


def test_workbench_events_use_active_task_and_order_contract(tmp_path):
    with SessionJournal(tmp_path, "events") as journal:
        backend = backend_for(journal)
        controller = SessionController(backend)
        begin(backend)
        call(backend, "set_copilot_stage", STAGE)
        publish(backend, list(backend.workbench.index.data))
        backend._finish("completed")
        cursor = EventCursor(journal.session_id, controller.runtime_id, controller.current)
        batch = controller.updates.read_updates(0)
        assert cursor.consume(batch) == journal.events()
        stale = deepcopy(next(e for e in batch["events"] if e["kind"].startswith("workbench.")))
        stale["sequence"] = cursor.sequence + 1
        with pytest.raises(ValueError, match="Stale task"):
            cursor.consume({**batch, "events": [stale]})
        controller.close()


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://example.invalid",
        "copilot://data/another/d_1_input",
        "copilot://data/session/../d_1_input",
        "copilot://data/session/d_1_input?x=1",
        "copilot://data/session/d_1_input#x",
        "copilot://data@external/session/d_1_input",
    ],
)
def test_unregistered_links_are_rejected(url):
    with pytest.raises(ValueError):
        parse_link(url, "session")
    assert parse_link(object_link("data", "session", "d_1_input"), "session") == (
        "data",
        "d_1_input",
    )
    # Links recorded before the rename keep resolving.
    assert parse_link("studio://data/session/d_1_input", "session") == ("data", "d_1_input")
