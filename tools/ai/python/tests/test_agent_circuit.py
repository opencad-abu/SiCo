"""Finite literal transport and shared write/preflight boundaries."""

import json
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
from test_agent_project import Broker, bound
from test_project_contract_boundaries import observations

from cadai.circuit_spec_schema import CircuitSpecError
from cadai.mcp import _TOOLS_BY_NAME
from cadai.mcp_registry import wire_row
from cadai.project_tools import call_project
from sico.adapters.circuit import CircuitClient, PreflightRequired
from sico.adapters.virtuoso import context_tools
from sico.core.contracts import ToolCall
from sico.storage.journal import SessionJournal
from sico.transport.circuit import encode_value, literal_call, wire_arguments


@pytest.mark.parametrize(
    "code",
    [
        "aiCreateExecute",
        "aiCreateExecute(",
        "aiCreateExecute(list(",
        'aiCreateExecute("a") exit()',
        'aiCreateExecute(evalstring("exit()"))',
        "aiCreateExecute(quote(a))",
        "exit()",
        "aiCreateExecute(abc)",
        "aiCreateExecute(1;hello)",
        "aiCreateExecute(1)()",
    ],
)
def test_native_parser_rejects_source(code):
    with pytest.raises((CircuitSpecError, ValueError)):
        literal_call(code)


def test_wire_preserves_literals_and_requires_write_claim():
    code = 'aiCreatePrepare("task:x" "r" "d" list("中文\\n\\"" nil t 1 -2 3.5 1e-9 list()))'
    function, values = literal_call(code)
    assert values[3] == ['中文\n"', None, True, 1, -2, 3.5, 1e-9, []]
    wire = wire_arguments(dict(function=function, values=values, claim=None, expected=None))
    assert "中文" not in wire[1] and "\n" not in wire[1] and '"' not in wire[1]
    with pytest.raises(ValueError, match="claim"):
        wire_arguments(
            dict(function="aiCreateExecute", values=["a", "b", "c"], claim=None, expected=None)
        )
    for value in [float("nan"), float("inf"), {}, 2**32, "bad\0text"]:
        with pytest.raises(ValueError):
            encode_value(value)


def test_native_literal_decoder(tmp_path):
    binary = shutil.which("dbAccess")
    if not binary:
        pytest.skip("dbAccess unavailable")
    root = Path(__file__).resolve().parents[2]
    values = ["", "中文", '\n"();', None, True, 0, -2, 0.0625, 1e-9, 1e20, ["x", [3]]]
    bad = ["s00", "sff0", "l2:s61", "i1x", "f1e+", "n:t", "l4097", "szz", "f1.0exit()"]
    script = tmp_path / "decode.il"
    lines = [
        f"load({json.dumps(str(root / 'skill' / name))})"
        for name in [
            "AI_protocol.il",
            "AI_eval.il",
            "AI_circuit.il",
            "AI_copilot_literals.il",
            "AI_copilot_targets.il", "AI_copilot_circuit.il",
        ]
    ]
    lines.append(
        'aiWriteTextFile("decoded.json" aiCircuitJson(aiCopilotDecode('
        + json.dumps(encode_value(values))
        + ")))"
    )
    for wire in bad:
        lines.append(
            "when(errset(aiCopilotDecode("
            + json.dumps(wire)
            + ') nil) error("Invalid literal accepted"))'
        )
    lines += ['aiWriteTextFile("passed" "yes")', "exit()"]
    script.write_text("\n".join(lines))
    result = subprocess.run(
        [binary, "-nocdsinit", "-load", str(script)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert (tmp_path / "passed").exists(), result.stdout + result.stderr
    assert json.loads((tmp_path / "decoded.json").read_text()) == values


class CircuitBroker(Broker):
    def __init__(self, path):
        super().__init__(path)
        self.writes = []
        self.expected = [
            "task:t",
            "background",
            ["Work", "Top", "schematic"],
            "schematic",
            "a" * 64,
            str(path),
            None,
        ]

    def circuit_call(self, context, params):
        wire_arguments(params)
        self.writes.append(deepcopy(params))
        fn = params["function"]
        if fn == "aiCopilotDescribe":
            return {
                "valid": True,
                "circuit": {"ok": True, "binding": self.expected, "model_files": []},
            }
        if fn == "aiProjectClaim":
            return {"valid": True, "circuit": {"ok": True}}
        if fn == "aiCreateExecute":
            assert params["claim"] and params["expected"] == self.expected
            self.client.receipt = {"ok": True, "saved": True, "circuit_ref": "created:make"}
            return {"valid": True, "circuit": self.client.receipt}
        pytest.fail(fn)


@pytest.mark.parametrize("intent", ["create", "edit"])
def test_preflight_coverage_and_duplicate_recovers_without_fresh_check(tmp_path, monkeypatch, intent):
    broker = CircuitBroker(tmp_path)
    original_call = broker.circuit_call

    def circuit_call(context, params):
        reply = original_call(context, params)
        if params["function"] == "aiCopilotDescribe":
            reply["circuit"]["target_intent"] = intent
        return reply

    monkeypatch.setattr(broker, "circuit_call", circuit_call)
    client = CircuitClient(broker, bound(tmp_path), tmp_path)
    arguments = {
        "operation": "create_circuit_from_plan",
        "arguments": {
            "prepare_ref": "create-prepare:p",
            "geometry_plan_digest": "a" * 64,
            "request_id": "make",
        },
    }
    with pytest.raises(PreflightRequired):
        call_project("execute_circuit_operation", arguments, client, tmp_path, _TOOLS_BY_NAME)
    assert not broker.writes
    args, _ = observations(tmp_path)
    client.requirements = args
    with pytest.raises(PreflightRequired, match="exact output"):
        call_project("execute_circuit_operation", arguments, client, tmp_path, _TOOLS_BY_NAME)
    target = dict(
        library="Work", cell="Top", view="schematic", intent=intent,
        oa_view_type="schematic"
    )
    client.requirements.update(
        presentation="background",
        targets=[target],
        libraries=[{"library": "Work", "expected_path": str(tmp_path)}],
    )
    client.requirements["directories"][0].update(path=str(tmp_path), purpose="project")
    original = client.call_project_native

    def native(fn, values):
        if fn == "aiProjectObserve":
            return {
                "ok": True,
                "gui_available": True,
                "missing_apis": [],
                "libraries": [["Work", str(tmp_path)]],
                "targets": [[["Work", "Top", "schematic"], intent == "edit",
                             "schematic" if intent == "edit" else None,
                             intent == "edit", True, intent == "edit", intent == "edit"]],
            }
        return original(fn, values)

    monkeypatch.setattr(client, "call_project_native", native)
    first = call_project("execute_circuit_operation", arguments, client, tmp_path, _TOOLS_BY_NAME)
    assert first["state"] == "retained_receipt", first
    client.requirements = None
    second = call_project("execute_circuit_operation", arguments, client, tmp_path, _TOOLS_BY_NAME)
    assert second["receipt"] == first["receipt"]
    assert sum(p["function"] == "aiCreateExecute" for p in broker.writes) == 1


@pytest.mark.parametrize("outcome", ["lost", "unknown", "partial"])
def test_shared_write_ambiguity_is_never_replayed(tmp_path, monkeypatch, outcome):
    broker = CircuitBroker(tmp_path)
    client = CircuitClient(broker, bound(tmp_path), tmp_path)
    monkeypatch.setattr(
        client,
        "prepare_operation",
        lambda *_: {
            "source": client.context.record(),
            "expected": broker.expected,
        },
    )
    original = broker.circuit_call

    def interrupted(context, params):
        response = original(context, params)
        if params["function"] == "aiCreateExecute":
            if outcome == "unknown":
                broker.client.receipt = None
            elif outcome == "partial":
                broker.client.receipt.update(
                    ok=False, saved=False, error="interrupted after target creation"
                )
            raise ConnectionError("native reply lost")
        return response

    monkeypatch.setattr(broker, "circuit_call", interrupted)
    args = {
        "operation": "create_circuit_from_plan",
        "arguments": {
            "prepare_ref": "create-prepare:p",
            "geometry_plan_digest": "a" * 64,
            "request_id": "make",
        },
    }
    first = call_project("execute_circuit_operation", args, client, tmp_path, _TOOLS_BY_NAME)
    expected_state = {
        "lost": "retained_receipt",
        "unknown": "outcome_unknown",
        "partial": "partial_or_failed",
    }[outcome]
    assert first["state"] == expected_state
    assert first["automatic_resume_allowed"] is False
    monkeypatch.setattr(client, "prepare_operation", lambda *_: pytest.fail("replay preflight"))
    retry = call_project("execute_circuit_operation", args, client, tmp_path, _TOOLS_BY_NAME)
    assert retry["state"] == expected_state
    assert sum(p["function"] == "aiCreateExecute" for p in broker.writes) == 1


def test_failed_fresh_check_prevents_intent_claim_and_write(tmp_path, monkeypatch):
    from test_agent_circuit_codex import BoundBroker

    from cadai.project_journal import OperationJournal

    broker = BoundBroker(tmp_path)
    client = CircuitClient(broker, bound(tmp_path), tmp_path)
    client.requirements = dict(
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
        model_files=[str(tmp_path / "vanished-model.scs")],
    )
    args = {
        "operation": "create_circuit_from_plan",
        "arguments": {
            "prepare_ref": "create-prepare:p",
            "geometry_plan_digest": "a" * 64,
            "request_id": "make",
        },
    }
    with pytest.raises(PreflightRequired, match="changed or failed"):
        call_project("execute_circuit_operation", args, client, tmp_path, _TOOLS_BY_NAME)
    assert client.requirements is None
    assert [p["function"] for p in broker.writes] == ["aiCopilotDescribe"]
    assert OperationJournal(tmp_path).read("create-execute:make") is None


def test_registration_reuses_shared_definitions_and_annotations(tmp_path):
    from sico.codex.mcp import ToolBridge

    with SessionJournal(tmp_path, "write") as journal:
        registry = context_tools(CircuitBroker(tmp_path), journal)
        from sico.transport.framing import FrameDecoder, encode

        bridge = ToolBridge(registry, lambda *_: None)
        try:
            # Exercise the actual bounded MCP response, including its envelope.
            # RunConfig.context_chars limits conversation compaction, not schemas.
            response = {"result": bridge.dispatch({"method": "list"})}
            assert FrameDecoder().feed(encode(response)) == [response]
            listed = response["result"]["tools"]
            schemas = {t["name"]: t for t in listed}
            assert len(schemas) == len(listed) == len(registry.schemas())
            for name in ("execute_circuit_operation", "begin_circuit_task", "preview_circuit_spec"):
                # The advertised row is the shared definition's wire projection;
                # domain metadata and on-demand help never reach the wire.
                assert schemas[name] == wire_row(_TOOLS_BY_NAME[name])
            assert "eval_skill" in schemas and "create_rc_circuit" in schemas
            definition = registry.execute(
                ToolCall(
                    "schema", "get_circuit_operation_schema", {"operation": "create_circuit_config"}
                ),
                bound(tmp_path),
            )
            assert definition.data["definition"] == _TOOLS_BY_NAME["create_circuit_config"]
            for name in ("preview_circuit_spec", "preview_circuit_geometry", "get_pdk_device",
                         "preflight_circuit_project", "evaluate_waveform_specs"):
                detail = registry.execute(
                    ToolCall("nested", "get_circuit_operation_schema", {"operation": name}),
                    bound(tmp_path),
                )
                assert detail.status == "ok"
                assert detail.data["definition"] == _TOOLS_BY_NAME[name]
                assert detail.data["dispatch_tool"] == name
            refused = registry.execute(
                ToolCall("raw", "get_circuit_operation_schema", {"operation": "eval_skill"}),
                bound(tmp_path),
            )
            assert refused.status == "invalid_arguments"
            result = registry.execute(
                ToolCall(
                    "no",
                    "execute_circuit_operation",
                    {
                        "operation": "create_circuit_config",
                        "arguments": {
                            "library": "Work",
                            "cell": "Top",
                            "top_library": "Work",
                            "top_cell": "Top",
                            "task_ref": "task:t",
                            "request_id": "make",
                        },
                    },
                ),
                bound(tmp_path),
            )
            assert result.status == "preflight_failed"
        finally:
            bridge.close()


@pytest.mark.parametrize("sections", [["ports"], ["geometry", "parameters"],
                                     ["ports", "ports"], ["unknown"], []])
def test_shared_pdk_sections_use_original_contract(tmp_path, monkeypatch, sections):
    from pdk_fixture import Bridge

    import cadai.mcp as mcp
    from cadai.pdk_schema import PdkArgumentError, arguments
    from cadai.pdk_session import PdkSession

    native = Bridge()
    service = PdkSession(bridge=native)
    monkeypatch.setattr(mcp, "PdkSession", lambda client, *, workspace, entry: service)
    with SessionJournal(tmp_path, "pdk-sections") as journal:
        registry = context_tools(CircuitBroker(tmp_path), journal)
        search = registry.execute(
            ToolCall("search", "search_pdk_devices", {"library": "synthetic"}),
            bound(tmp_path),
        )
        assert search.status == "ok"
        inputs = {"snapshot_ref": search.data["context"]["snapshot_ref"],
                  "device_ref": search.data["items"][0]["device_ref"], "sections": sections}
        before = len(native.calls)
        try:
            arguments("get_pdk_device", inputs)
            valid = True
        except PdkArgumentError:
            valid = False
        result = registry.execute(ToolCall("detail", "get_pdk_device", inputs), bound(tmp_path))
        if valid:
            assert result.status == "ok"
            assert len(native.calls) == before + 1
            assert result.data == service.call("get_pdk_device", inputs)
        else:
            assert result.status == "invalid_arguments"
            assert len(native.calls) == before
