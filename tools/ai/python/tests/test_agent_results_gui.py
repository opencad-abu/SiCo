"""Foreground shared relay analysis, with exact History and borrowed ADE scope."""

import copy
import json
import math
import os
import shlex
import sys

import pytest
from ac_fixture import ac_setup, point_recipes
from ac_response_fixture import recipe as response_recipe
from circuit_create_support import AI_ROOT
from template_smic28_support import migration_session
from test_circuit_rc_gui import mcp_call
from test_simulation_recipe_failures_gui import terminal
from test_simulation_recipe_gui import load_backend
from test_waveform_measure import quantity
from waveform_pair_fixture import measurement_recipes, setup_recipe

from sico.adapters.virtuoso import context_tools
from sico.core.contracts import NeedsReconcile, ToolCall
from sico.storage.journal import SessionJournal
from sico.transport.broker import ContextBroker

pytestmark = pytest.mark.skipif(
    os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1", reason="licensed foreground shared analysis"
)


def test_shared_history_waveform_and_report(tmp_path):
    with migration_session(tmp_path) as (bridge, server, proc):
        bridge.skill("load(" + json.dumps(str(AI_ROOT.parent / "sico/skill++/SICO.ils")) + ")")
        bridge.skill('ddCreateLib("genericWork" ' + json.dumps(str(tmp_path / "worklib")) + ")")
        load_backend(bridge, tmp_path)
        bridge.skill("load(" + json.dumps(str(AI_ROOT / "python/tests/waveform_pair_fixture.il")) + ")")
        assert bridge.skill("pairFixtureCreate()") == "t"
        bridge.skill(
            'let((agrCv agrV) agrCv=dbOpenCellViewByType("genericWork" "paired_rc" "schematic" "" "a") '
            'agrV=dbFindAnyInstByName(agrCv "V1") dbReplaceProp(agrV "acm" "string" "2") '
            'dbReplaceProp(agrV "acp" "string" "30") schCheck(agrCv) dbSave(agrCv) dbClose(agrCv))'
        )

        def original(name, **args):
            return mcp_call(server, name, args)

        task = original("begin_circuit_task", task_id="analysis_fixture", presentation="foreground", choice_confirmed=True)
        recipe = ac_setup()
        recipe["tests"].append(copy.deepcopy(setup_recipe()["tests"][0]))
        recipe["tests"][1]["outputs"].append(dict(name="saved_sample", kind="expression", value='value(VT("/OUT") 2e-6)', plot=False, save=True))
        preview = original("preview_simulation_recipe", recipe=recipe)
        setup = original("create_simulation_setup", recipe=recipe, recipe_digest=preview["recipe_digest"],
                         task_ref=task["task_ref"], request_id="analysis_fixture")
        assert setup["ok"], setup
        before = original("inspect_simulation_setup", setup_ref=setup["setup_ref"])
        run = original("run_simulation_setup", setup_ref=setup["setup_ref"], request_id="analysis_fixture")
        assert terminal(server, run)["state"] == "completed"
        bridge.skill('agrSetup=aiSimulationSetups[' + json.dumps(setup["setup_ref"]) + ']')
        with SessionJournal(tmp_path, "analysis") as journal, ContextBroker(25) as broker:
            configuration = journal.directory / "relay.json"
            configuration.write_text(json.dumps(dict(host=broker.address[0], port=broker.address[1], token=broker.token)))
            configuration.chmod(0o600)
            command = shlex.join([sys.executable, "-s", str(AI_ROOT.parent / "sico/python/sico-entry"),
                                  "relay", "--connection-file", str(configuration)])
            # The task begins in an already-open ADE; no open_design_view/new window needed.
            bridge.skill('agrCid=sicoConnect(' + json.dumps(command) + ' "analysis" "generation" agrSetup[\'window])')
            evidence = []
            try:
                context = broker.context("analysis", timeout=10)
                registry = context_tools(broker, journal)

                def call(name, **args):
                    result = registry.execute(ToolCall("analysis", name, args), context)
                    evidence.append(dict(tool=name, result=result.record()))
                    assert result.status == "ok", result
                    return result.data

                bound = call("bind_result_context", history=run["history"])
                assert bound["target"] == before["target"]
                assert call("bind_result_context", history=run["history"])["context_ref"] == bound["context_ref"]
                capture = dict(context_ref=bound["context_ref"], history=run["history"])
                anchor = call("read_maestro_results", **capture)
                assert anchor["test_points"] == 8
                call("query_maestro_results", result_ref=anchor["result_ref"], entity="coordinates", limit=2)
                point = next(p for p in preview["expected_test_points"] if p["test"] == "small_signal")
                ac_args = dict(capture, **point, analysis="ac")
                ac_in = call("read_maestro_ac_waveform", **ac_args, signal="/IN")
                ac_out = call("read_maestro_ac_waveform", **ac_args, signal="/OUT")
                assert ac_out["sample_count"] == 401
                assert (ac_out["native_y_type"], ac_out["x_unit"], ac_out["y_unit"]) == ("doublecomplex", "Hz", "V")
                assert ac_out["source"]["result_ref"] == anchor["result_ref"]
                single = call("measure_ac_waveform", waveform_ref=ac_out["waveform_ref"], recipes=point_recipes(1e6))
                pair_args = dict(input_ref=ac_in["waveform_ref"], output_ref=ac_out["waveform_ref"],
                                 denominator_floor=quantity(1, "mV"), recipes=point_recipes(1e6, True))
                pair = call("measure_ac_transfer", **pair_args)
                assert single["rows"][2]["value"] - pair["rows"][2]["value"] == pytest.approx(30, abs=1e-10)
                bridge.skill('agrIn=getData("/IN" ?result \'ac ?resultsDir ' + json.dumps(ac_in["source"]["results_directory"]) + ')')
                bridge.skill('agrOut=getData("/OUT" ?result \'ac ?resultsDir ' + json.dumps(ac_out["source"]["results_directory"]) + ')')
                native = json.loads(bridge.skill('aiCircuitJson(list(mag(value(agrOut/agrIn 1e6)) phaseDeg(value(agrOut/agrIn 1e6))))'))
                assert [pair["rows"][0]["value"], pair["rows"][2]["value"]] == pytest.approx(native, abs=1e-10)
                response_args = dict(pair_args, recipes=[response_recipe("bandwidth"), response_recipe("phase_margin")])
                response = call("measure_ac_response", **response_args)
                rows = call("query_ac_response", report_ref=response["report_ref"], entity="rows")["items"]
                resistance = next(v["value"] for v in ac_out["source"]["coordinate"]["parameters"] if v["name"] == "RVAL")
                assert rows[0]["value"] == pytest.approx(math.sqrt(10**0.3 - 1) / (2 * math.pi * float(resistance) * 1e-9), rel=0.001)
                assert rows[1]["value"] is None  # Passive fixture has no unity-gain crossing.
                tran_point = dict(point, test="transient_response")
                tran_args = dict(capture, **tran_point, analysis="tran")
                tran_in = call("read_maestro_waveform", **tran_args, signal="/IN")
                tran_out = call("read_maestro_waveform", **tran_args, signal="/OUT")
                assert (tran_out["x_unit"], tran_out["y_unit"]) == ("s", "V")
                tran_pair = call("measure_waveform_pair", input_ref=tran_in["waveform_ref"], output_ref=tran_out["waveform_ref"], recipes=measurement_recipes())
                assert tran_pair["all_measured"]
                scalar_points = [p for p in preview["expected_test_points"] if p["test"] == "small_signal"]
                specs = dict(source="Explicit shared relay acceptance", expected_test_points=scalar_points,
                             specifications=[dict(test="small_signal", output="saved_mag", unit="V", lower=0, upper=2)])
                scalar = call("evaluate_measurement_specs", result_ref=anchor["result_ref"], contract=specs)
                # Native RDBs may omit a saved expression for a point even when
                # the run is complete; the generic contract reports that as
                # incomplete rather than manufacturing a value.
                assert scalar["qualification"] in {"pass", "incomplete"}
                assert scalar["qualification"] == "incomplete" or scalar["coverage_complete"]
                spec = dict(test=point["test"], analysis="ac", kind="ac_transfer", signals=dict(input="/IN", output="/OUT"),
                            recipe=pair_args["recipes"][0], denominator_floor=pair_args["denominator_floor"], unit="1", lower=0, upper=1)
                tran_spec = dict(test="transient_response", analysis="tran", kind="pair", signals=dict(input="/IN", output="/OUT"),
                                 recipe=measurement_recipes()[0], unit="ns", lower=0, upper=10000)
                wave_contract = dict(source="Explicit incomplete coverage acceptance", expected_test_points=preview["expected_test_points"], specifications=[spec, tran_spec])
                wave_args = dict(result_ref=anchor["result_ref"], measurement_refs=[pair["measurement_ref"], tran_pair["measurement_ref"]], contract=wave_contract)
                report = call("evaluate_waveform_specs", **wave_args)
                assert report["qualification"] == "incomplete" and report["counts"]["missing"] == 6

                # A newer run must not retarget the original result context.
                newer = original("run_simulation_setup", setup_ref=setup["setup_ref"], request_id="analysis_newer")
                assert terminal(server, newer)["state"] == "completed" and newer["history"] != run["history"]
                assert call("read_maestro_ac_waveform", **ac_args, signal="/OUT")["waveform_ref"] == ac_out["waveform_ref"]
                with pytest.raises(NeedsReconcile):
                    call("read_maestro_results", **dict(capture, history=newer["history"]))

                # Authoritative native guard rejects unowned setup and captured test overrides.
                with pytest.raises(NeedsReconcile):
                    call("bind_result_context", history=run["history"], setup_ref=setup["setup_ref"])
                # Model a retained setup that was created through the shared source-owned gate.
                bridge.skill('aiCopilotTasks[agrSetup[\'task]]=sicoBindings[agrCid]')
                assert call("bind_result_context", history=run["history"], setup_ref=setup["setup_ref"])["context_ref"] == bound["context_ref"]
                bridge.skill('sicoBindings[agrCid][\'testName]="small_signal"')
                scoped = call("bind_result_context", history=run["history"])
                assert scoped["context_ref"] != bound["context_ref"]
                with pytest.raises(NeedsReconcile):
                    call("read_maestro_waveform", **dict(tran_args, context_ref=scoped["context_ref"]), signal="/OUT")
                bridge.skill('sicoBindings[agrCid][\'testName]=nil')
                bridge.skill('agrBound=aiCopilotResultBindings[' + json.dumps(bound["context_ref"]) + ']')
                bridge.skill('agrOldHandle=agrBound[\'historyHandle]')
                bridge.skill('agrBound[\'historyHandle]=axlGetHistoryEntry(axlGetMainSetupDB(agrSetup[\'session]) ' + json.dumps(newer["history"]) + ')')
                with pytest.raises(NeedsReconcile):
                    call("read_maestro_results", **capture)
                bridge.skill('agrBound[\'historyHandle]=agrOldHandle')
                bridge.skill('sicoBindings[agrCid][\'historyName]=' + json.dumps(run["history"]))
                bridge.skill('sicoBindings[agrCid][\'historyHandle]=agrOldHandle')
                with pytest.raises(NeedsReconcile):
                    call("bind_result_context", history=newer["history"])
                bridge.skill('sicoBindings[agrCid][\'historyName]=nil')
                bridge.skill('sicoBindings[agrCid][\'historyHandle]=nil')
                assert original("inspect_simulation_setup", setup_ref=setup["setup_ref"]) == before

                # Freeze a private page, then close the borrowed ADE. Cleanup still works;
                # immutable analysis has no broker dependency and capture must refuse.
                frozen = broker.circuit_call(context, dict(function="aiResultsCapture", values=[bound["context_ref"], run["history"], "frozen"], claim=None, expected=None))
                assert frozen["circuit"]["pages"] >= 1
                # Another captured source cannot page or release this token.
                for fn in ("aiResultsPage", "aiResultsRelease"):
                    values = 'list("frozen" 0)' if fn.endswith("Page") else 'list("frozen")'
                    assert bridge.skill('errset(aiCopilotAnalysisPageCall(makeTable("foreign" nil) ' + json.dumps(fn) + ' ' + values + ') nil)') == "nil"
                bridge.skill('hiCloseWindow(agrSetup[\'window])')
                for function, values in [("aiResultsPage", ["frozen", 0]), ("aiResultsRelease", ["frozen"])]:
                    assert broker.circuit_call(context, dict(function=function, values=values, claim=None, expected=None))["circuit"]["ok"]
                with pytest.raises(NeedsReconcile):
                    call("read_maestro_results", **capture)
                assert call("measure_ac_transfer", **pair_args) == pair
                assert call("evaluate_waveform_specs", **wave_args) == report
                call("query_waveform_spec_report", report_ref=report["report_ref"], limit=2)
                call("query_ac_waveform", waveform_ref=ac_out["waveform_ref"], offset=256, limit=100)
                call("query_waveform", waveform_ref=tran_out["waveform_ref"], limit=2)
                assert bridge.skill('aiCircuitJson(list(length(aiResultsCaptures) length(aiWaveCaptures) length(aiCopilotResultPages) length(aiCopilotWavePages)))') == "[0,0,0,0]"
                (tmp_path / "evidence.json").write_text(json.dumps(dict(events=evidence, setup=setup, run=run, newer=newer, native=native, pid=proc.pid), indent=2))
            finally:
                bridge.skill("progn(sicoDisconnect(agrCid) t)")
