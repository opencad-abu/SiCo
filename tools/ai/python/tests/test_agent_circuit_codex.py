"""Real 0.154 MCP write dispatch; CAD evidence injected, no production provider used."""

import json

import pytest
from test_agent_circuit import CircuitBroker
from test_agent_codex_native import answer, gateway, native_codex, settings, sse  # noqa: F401
from test_agent_project import bound

from sico.adapters.virtuoso import context_tools
from sico.codex.backend import CodexBackend
from sico.storage.journal import SessionJournal


class BoundBroker(CircuitBroker):
    def get_context(self, context):
        return {"valid": True}

    def read(self, context, method, params):
        if method == "project_observe":
            self.calls.append((context.record(), method, params))
            return {
                "valid": True,
                "project": {
                    "ok": True,
                    "gui_available": True,
                    "missing_apis": [],
                    "libraries": [["Work", str(self.path)]],
                    "targets": [
                        [["Work", "Top", "schematic"], False, None, False, True, False, False]
                    ],
                },
            }
        return super().read(context, method, params)


@pytest.mark.parametrize("tool_format", ["flat", "native"])
def test_actual_codex_journalled_write_and_replay(tmp_path, native_codex, gateway, tool_format):  # noqa: F811
    requirements = dict(
        presentation="background",
        targets=[
            dict(
                library="Work", cell="Top", view="schematic",
                intent="create", oa_view_type="schematic"
            )
        ],
        libraries=[dict(library="Work", expected_path=str(tmp_path))],
        directories=[
            dict(path=str(tmp_path), purpose="project", min_free_bytes=1, min_free_inodes=1)
        ],
        model_files=[],
    )
    operation = dict(
        operation="create_circuit_from_plan",
        arguments={
            "prepare_ref": "create-prepare:p",
            "geometry_plan_digest": "a" * 64,
            "request_id": "make",
        },
    )
    for index, (name, args) in enumerate(
        [
            ("preflight_circuit_project", requirements),
            ("execute_circuit_operation", operation),
            ("execute_circuit_operation", operation),
        ]
    ):
        identity = (
            {"name": "mcp__sico_ic__" + name}
            if tool_format == "flat"
            else {
                "namespace": "mcp__sico_ic",
                "name": name,
            }
        )
        gateway.responses.put(
            sse(
                {
                    "type": "function_call",
                    "id": "fc-" + str(index),
                    "call_id": "call-" + str(index),
                    "arguments": json.dumps(args),
                    **identity,
                }
            )
        )
    gateway.responses.put(answer("已读取保存回执；重复请求没有重新创建。"))
    broker = BoundBroker(tmp_path)
    with SessionJournal(tmp_path, "write-codex") as journal:
        registry = context_tools(broker, journal)
        backend = CodexBackend(
            settings(gateway, native_codex, tool_format), registry, journal, bound(tmp_path)
        )
        try:
            events = list(backend.run("按已确认的后台任务创建准备好的电路", input_id="write"))
            assert events[-1]["kind"] == "task.completed", events
            finished = [
                e["payload"]
                for e in events
                if e["kind"] == "tool.finished"
                and e["payload"]["name"] == "execute_circuit_operation"
            ]
            assert len(finished) == 2, events
            assert all(e["result"]["status"] == "ok" for e in finished)
            assert (
                finished[0]["result"]["data"]["receipt"] == finished[1]["result"]["data"]["receipt"]
            )
            assert sum(p["function"] == "aiCreateExecute" for p in broker.writes) == 1
        finally:
            backend.close()
