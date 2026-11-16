"""Shared schemas/math, immutable recovery, and no replay on interrupted capture."""

import json
from copy import deepcopy

import pytest
from ac_spec_fixture import fixture as ac_fixture
from test_agent_project import bound
from test_result_contract import fixture_data
from waveform_spec_fixture import fixture as tran_fixture

from cadai.mcp import _TOOLS_BY_NAME, McpServer
from cadai.result_tools import ResultStore
from sico.adapters.results import NAMES
from sico.adapters.virtuoso import context_tools
from sico.core.contracts import ModelTurn, NeedsReconcile, ToolCall
from sico.service.python_backend import PythonBackend
from sico.providers.mock import ScriptedProvider
from sico.storage.journal import SessionJournal
from sico.transport.circuit import wire_arguments


class Offline:
    def __getattr__(self, name):
        if name == "reconcile_binding_operation":
            # Registry construction probes this capability with getattr; only
            # an actual call would contact the native transport.
            return None
        pytest.fail("Offline analysis contacted native transport: " + name)


def execute(registry, path, name, **args):
    result = registry.execute(ToolCall("analysis", name, args), bound(path))
    assert result.status == "ok", result
    return result.data


def test_all_shared_definitions_match_and_offline_algorithms_are_identical(tmp_path):
    store, args, reports, waves = ac_fixture(tmp_path, label="arbitrary_current_sensor", unit="mA", y_scale=1000)
    with SessionJournal(tmp_path, "analysis") as journal:
        registry = context_tools(Offline(), journal)
        schemas = {d["name"]: d for d in registry.schemas()}
        for name in NAMES:
            assert schemas[name]["input_schema"] == _TOOLS_BY_NAME[name]["inputSchema"]
            assert schemas[name]["description"] == _TOOLS_BY_NAME[name]["description"]
            assert schemas[name]["annotations"] == _TOOLS_BY_NAME[name]["annotations"]
        catalog = execute(registry, tmp_path, "list_measurement_capabilities", limit=50)
        for item in catalog["items"]:
            execute(registry, tmp_path, "get_measurement_capability", capability_id=item["capability_id"], catalog_revision=catalog["catalog_revision"])
        queries = [
            ("query_maestro_results", dict(result_ref=args["result_ref"], entity="coordinates")),
            ("query_ac_waveform", dict(waveform_ref=waves[0][1], limit=1)),
        ]
        for kind, tool in [("ac_signal", "measure_ac_waveform"), ("ac_transfer", "measure_ac_transfer"), ("ac_response", "measure_ac_response")]:
            specs = [s for s in args["contract"]["specifications"] if s["kind"] == kind]
            inputs = dict(waveform_ref=waves[0][1]) if kind == "ac_signal" else dict(input_ref=waves[0][0], output_ref=waves[0][1], denominator_floor=specs[0]["denominator_floor"])
            queries.append((tool, dict(inputs, recipes=[s["recipe"] for s in specs])))
        queries.append(("evaluate_waveform_specs", args))
        original = McpServer(Offline(), workspace=tmp_path)
        for name, inputs in queries:
            shared = execute(registry, tmp_path, name, **inputs)
            raw = original.handle({"id": 1, "method": "tools/call", "params": {"name": name, "arguments": inputs}})
            assert shared == json.loads(raw["result"]["content"][0]["text"])
        assert shared["qualification"] == "pass"
        execute(registry, tmp_path, "query_waveform_spec_report", report_ref=shared["report_ref"], limit=2)
        execute(registry, tmp_path, "query_ac_response", report_ref=reports[2]["report_ref"], entity="crossings")
        # Recreated registry/server has no memory of capture; only immutable references matter.
        registry = context_tools(Offline(), journal)
        assert execute(registry, tmp_path, "evaluate_waveform_specs", **args) == shared
        args["measurement_refs"].pop()
        incomplete = execute(registry, tmp_path, "evaluate_waveform_specs", **args)
        assert incomplete["qualification"] == "incomplete" and incomplete["counts"]["missing"] > 0
        page = execute(registry, tmp_path, "query_waveform_spec_report", report_ref=incomplete["report_ref"], status="missing")
        assert all(r["value"] is None for r in page["items"])
        # Same metric ID with a different recipe is not silently accepted.
        args["contract"]["specifications"][0]["recipe"]["at"]["value"] += 1
        mismatch = execute(registry, tmp_path, "evaluate_waveform_specs", **args)
        assert mismatch["qualification"] == "incomplete"
        value = store.get(waves[0][1])
        value["source"]["history"] = "another_history"
        wrong, _ = store.put("ac_waveform", value)
        denied = registry.execute(ToolCall("mismatch", "measure_ac_transfer", dict(input_ref=waves[0][0], output_ref=wrong, denominator_floor={"value": 0, "unit": "A"}, recipes=queries[3][1]["recipes"])), bound(tmp_path))
        assert denied.status == "tool_error"
        path = store.directory() / (args["result_ref"] + ".json")
        path.write_text("{}")
        corrupt = registry.execute(ToolCall("corrupt", "query_maestro_results", {"result_ref": args["result_ref"]}), bound(tmp_path))
        assert corrupt.status == "tool_error" and "artifact" in corrupt.data["message"]


def test_tran_reports_and_engineering_fail_are_successful_tools(tmp_path):
    store, args, singles, pairs = tran_fixture(tmp_path)
    with SessionJournal(tmp_path, "tran") as journal:
        registry = context_tools(Offline(), journal)
        report = execute(registry, tmp_path, "evaluate_waveform_specs", **args)
        assert report["qualification"] == "pass"
        # Recompute original generic recipes through Copilot's registered handlers.
        single = store.get(singles[0]["measurement_ref"])
        pair = store.get(pairs[0]["measurement_ref"])
        assert execute(registry, tmp_path, "measure_waveform", waveform_ref=single["waveform_ref"], recipes=[args["contract"]["specifications"][0]["recipe"]]) == singles[0]
        assert execute(registry, tmp_path, "measure_waveform_pair", input_ref=pair["input_ref"], output_ref=pair["output_ref"], recipes=[args["contract"]["specifications"][1]["recipe"]]) == pairs[0]
        execute(registry, tmp_path, "query_waveform", waveform_ref=single["waveform_ref"], limit=1)
        _, _, snapshot, contract = fixture_data()
        ref, _ = store.put("results", snapshot)
        result = execute(registry, tmp_path, "evaluate_measurement_specs", result_ref=ref, contract=contract)
        assert result["qualification"] == "fail" and result["spec_qualified"] is False
        execute(registry, tmp_path, "query_maestro_results", result_ref=result["report_ref"], entity="rows")
        with pytest.raises(NeedsReconcile):
            registry.execute(ToolCall("other", "query_maestro_results", {"result_ref": ref}), bound(tmp_path.parent))


class CaptureBroker:
    def __init__(self, failure=None):
        self.failure, self.calls = failure, []

    def get_context(self, context):
        return dict(context.snapshot, valid=True)

    def circuit_call(self, context, envelope):
        wire_arguments(envelope)
        self.calls.append(deepcopy(envelope))
        name, values = envelope["function"], envelope["values"]
        meta, records, _, _ = fixture_data()
        if name == "aiResultsCapture":
            data = dict(meta, ok=True, context_ref=values[0], history=values[1], pages=2)
        elif name == "aiResultsPage":
            if values[1] == 1 and self.failure == "disconnect":
                raise NeedsReconcile("Exact source disconnected during capture")
            data = dict(ok=True, records=records[:3] if values[1] == 0 else records[3:])
            if values[1] == 1 and self.failure == "truncated":
                data["records"] = []
        elif name == "aiResultsRelease":
            data = dict(ok=True)
        else:
            pytest.fail(name)
        return dict(valid=True, circuit=data)


@pytest.mark.parametrize("failure", [None, "disconnect", "truncated"])
def test_capture_publication_and_cleanup_boundary(tmp_path, failure):
    broker = CaptureBroker(failure)
    with SessionJournal(tmp_path, "capture") as journal:
        registry = context_tools(broker, journal)
        call = ToolCall("capture", "read_maestro_results", dict(context_ref="copilot-results:bound", history="CustomHistory.9"))
        if failure == "disconnect":
            with pytest.raises(NeedsReconcile):
                registry.execute(call, bound(tmp_path))
        else:
            result = registry.execute(call, bound(tmp_path))
            assert result.status == ("ok" if failure is None else "tool_error")
        assert [c["function"] for c in broker.calls] == ["aiResultsCapture", "aiResultsPage", "aiResultsPage", "aiResultsRelease"]
        assert len(list(ResultStore(tmp_path).directory().glob("results_*.json"))) == (1 if failure is None else 0)


def test_disconnect_pauses_queued_live_tools_without_recapture(tmp_path):
    broker = CaptureBroker("disconnect")
    calls = tuple(ToolCall(str(n), "read_maestro_results", dict(context_ref="copilot-results:bound", history="CustomHistory.9")) for n in (1, 2))
    with SessionJournal(tmp_path, "loop") as journal:
        registry = context_tools(broker, journal)
        loop = PythonBackend(ScriptedProvider([[ModelTurn(text="", calls=calls)]]), registry, journal, bound(tmp_path))
        events = list(loop.run("Read exact bound history"))
        assert events[-1]["kind"] == "task.needs_reconcile", events
        assert sum(c["function"] == "aiResultsCapture" for c in broker.calls) == 1
