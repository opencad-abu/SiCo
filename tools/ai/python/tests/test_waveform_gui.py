"""Native exact-history RC waveform and offline recipes; optional retained foreground."""

import json
import math
import os
import time

import pytest
from template_smic28_support import migration_session
from test_circuit_rc_gui import AI_ROOT, mcp_call
from test_waveform_measure import quantity, recipe


def test_exact_history_waveform_recipes(tmp_path):
    if os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1":
        pytest.skip("licensed Virtuoso/Spectre acceptance")
    with migration_session(tmp_path) as (bridge, server, proc):
        library = "waveWork"
        test = "response_unique"
        bridge.skill('ddCreateLib("waveWork" ' + json.dumps(str(tmp_path / "wave_library")) + ")")
        bridge.skill(
            'envSetVal("asimenv.startup" "projectDir" \'string '
            + json.dumps(str(tmp_path / "simulation"))
            + ")"
        )
        for m in ["AI_circuit_rc", "AI_maestro_results", "AI_maestro_waveform"]:
            bridge.skill("load(" + json.dumps(str(AI_ROOT / "skill" / (m + ".il"))) + ")")

        def call(name, **args):
            return mcp_call(server, name, args)

        task = call(
            "begin_circuit_task",
            task_id="wave_task",
            presentation="foreground",
            choice_confirmed=True,
        )
        pre = call("preview_rc_circuit", library=library, cell="network")
        created = call(
            "create_rc_circuit",
            library=library,
            cell="network",
            task_ref=task["task_ref"],
            request_id="network",
            preview_digest=pre["preview_digest"],
        )
        assert created["saved"], created
        bridge.skill("load(" + json.dumps(str(AI_ROOT / "python/tests/waveform_fixture.il")) + ")")
        end = time.monotonic() + 160
        while time.monotonic() < end:
            if bridge.skill('if(resultFinished "done" "running")') == "done":
                break
            time.sleep(0.5)
        assert bridge.skill('if(resultFinished "done" "running")') == "done"
        history = bridge.skill("resultHistory")
        opened = call(
            "open_design_view",
            library=library,
            cell="measurements",
            view="maestro",
            oa_view_type="maestro",
            presentation="foreground",
            access="read",
            request_id="read_wave",
        )
        assert opened["ok"], opened
        setup_before = bridge.skill(
            'aiCircuitJson(maeGetTestOutputs("response_unique" ?session resultSession))'
        )
        bridge.skill(
            "progn(waveRdb=maeReadResDB(?session resultSession ?historyName resultHistory) "
            'waveDir=waveRdb->test("response_unique" "warm" 1)->resultsDir '
            'openResults(strcat(waveDir "/psf")) selectResult(\'dcOp) t)'
        )
        selection_before = bridge.skill('aiCircuitJson(list(openResults() getData("/OUT")))')
        assert json.loads(selection_before)[1] == pytest.approx(1.0)
        args = dict(
            context_ref=opened["context_ref"],
            history=history,
            test=test,
            corner="cool",
            point=1,
            analysis="tran",
            signal="/OUT",
        )
        captured = call("read_maestro_waveform", **args)
        assert captured["ok"], captured
        assert captured["status"] == "waveform" and captured["sample_count"] > 256, captured
        assert (captured["x_unit"], captured["y_unit"]) == ("s", "V"), captured
        ref = captured["waveform_ref"]
        assert call("read_maestro_waveform", **args)["waveform_ref"] == ref
        warm = call("read_maestro_waveform", **dict(args, corner="warm"))
        assert warm["ok"] and warm["waveform_ref"] != ref, warm
        assert (
            warm["source"]["coordinate"]["parameters"]
            != captured["source"]["coordinate"]["parameters"]
        )
        for changes in [dict(history="Missing.99"), dict(test="missing"), dict(point=9)]:
            assert not call("read_maestro_waveform", **dict(args, **changes))["ok"]
        missing = call("read_maestro_waveform", **dict(args, signal="/not_saved_signal"))
        assert missing["ok"] and missing["status"] == "missing", missing
        window = dict(start=quantity(1.001, "us"), stop=quantity(3.001, "us"))
        recipes = [recipe("sample", at=quantity(2.001, "us"), result_unit="mV")]
        recipes += [recipe(op, window=window) for op in ["minimum", "maximum", "mean", "rms"]]
        recipes += [
            recipe("crossing", window=window, result_unit="us", threshold=quantity(500, "mV"))
        ]
        measured = call("measure_waveform", waveform_ref=ref, recipes=recipes)
        assert measured["all_measured"], measured
        values = {r["id"]: r["value"] for r in measured["rows"]}
        assert values["sample"] == pytest.approx(1000 * (1 - math.exp(-1)), abs=2)
        assert values["minimum"] == pytest.approx(0, abs=0.002)
        assert values["maximum"] == pytest.approx(1 - math.exp(-2), abs=0.002)
        assert values["mean"] == pytest.approx(1 - (1 - math.exp(-2)) / 2, abs=0.002)
        assert values["rms"] == pytest.approx(
            math.sqrt(1 - (1 - math.exp(-2)) + (1 - math.exp(-4)) / 4), abs=0.002
        )
        assert values["crossing"] == pytest.approx(1.001 + math.log(2), abs=0.003)
        scalar = call("read_maestro_results", context_ref=opened["context_ref"], history=history)
        native = call(
            "query_maestro_results",
            result_ref=scalar["result_ref"],
            test=test,
            corner="cool",
            output="sample_at_t",
        )["items"][0]["value"]
        assert values["sample"] / 1000 == pytest.approx(native, abs=1e-12)
        assert (
            bridge.skill(
                'aiCircuitJson(maeGetTestOutputs("response_unique" ?session resultSession))'
            )
            == setup_before
        )
        assert bridge.skill("length(aiWaveCaptures)") == "0"
        assert bridge.skill("length(aiResultsCaptures)") == "0"
        assert (
            bridge.skill('aiCircuitJson(list(openResults() getData("/OUT")))') == selection_before
        )
        reference = json.loads(
            bridge.skill(
                'progn(waveNative=getData("/OUT" ?result \'tran ?resultsDir '
                + json.dumps(captured["source"]["results_directory"])
                + ") waveClip=clip(waveNative 1.001e-6 3.001e-6 ?interpolate t) "
                "aiCircuitJson(list(average(waveClip ?continuous t ?overall nil) "
                "cross(waveClip 0.5 1 'rising))))"
            )
        )
        assert values["mean"] == pytest.approx(reference[0], abs=1e-12)
        assert values["crossing"] * 1e-6 == pytest.approx(reference[1], abs=1e-18)
        # famIsFamily documents nil for an empty family; a populated family stays unflattened.
        assert bridge.skill('car(aiWaveFreeze(famCreateFamily("sweep" \'double)))') == "error"
        assert (
            bridge.skill(
                'car(aiWaveFreeze(famAddValue(famCreateFamily("sweep" \'double) 1.0 waveNative)))'
            )
            == "family"
        )
        assert bridge.skill("car(aiWaveFreeze(1.0))") == "scalar"
        assert (
            bridge.skill(
                "car(aiWaveFreeze(drCreateWaveform(drCreateVec('double 2) "
                "drCreateVec('doublecomplex 2))))"
            )
            == "complex"
        )
        assert (
            bridge.skill(
                "car(aiWaveFreeze(drCreateWaveform(drCreateVec('double 2) drCreateVec('string 2))))"
            )
            == "error"
        )
        assert (
            bridge.skill(
                "let((guardData guardWave) for(guardIndex 0 65536 "
                "guardData=cons(guardIndex guardData)) guardData=reverse(guardData) "
                "guardWave=drCreateWaveform(drCreateVec('double guardData) "
                "drCreateVec('double guardData)) "
                "unless(drVectorLength(drGetWaveformXVec(guardWave))==65537 "
                'error("test waveform is not populated")) errset(aiWaveFreeze(guardWave) nil))'
            )
            == "nil"
        )
        # Advance the visible GUI to a new history; the old request remains exact.
        bridge.skill(
            "progn(resultFinished=nil newerHistory=maeRunSimulation("
            '?session resultSession ?callback "resultDone" ?waitUntilDone nil) t)'
        )
        end = time.monotonic() + 160
        while time.monotonic() < end:
            if bridge.skill('if(resultFinished "done" "running")') == "done":
                break
            time.sleep(0.5)
        assert bridge.skill('if(resultFinished "done" "running")') == "done"
        assert bridge.skill("newerHistory") != history
        assert call("read_maestro_waveform", **args)["waveform_ref"] == ref
        if os.environ.get("CAD_AI_TEMPLATE_FOREGROUND_DISPLAY"):
            bridge.skill(
                "awvPlotWaveform(awvCreatePlotWindow() list(waveNative) "
                '?expr list("OUT: Interactive.0 / cool"))'
            )
        call("release_design_context", context_ref=opened["context_ref"])
        assert call("measure_waveform", waveform_ref=ref, recipes=recipes) == measured
        assert (
            call("query_waveform", waveform_ref=ref, limit=1)["total"] == captured["sample_count"]
        )
        assert not call("read_maestro_waveform", **args)["ok"]
        bridge.skill("procedure(witMenuToolsRoot() " + json.dumps(str(AI_ROOT.parent)) + ")")
        for _ in range(2):
            bridge.skill("load(" + json.dumps(str(AI_ROOT / "skill++/AI.ils")) + ")")
        assert bridge.skill("aiWaveRevision()") == "20260911.ai.waveform.v2"
        assert bridge.skill("aiLoaderRevision()") == "20260915.ai.loader.v30"
        (tmp_path / "acceptance.json").write_text(
            json.dumps(
                dict(
                    captured=captured,
                    warm=warm,
                    measured=measured,
                    missing=missing,
                    native_reference=reference,
                    selection_unchanged=True,
                    old_history_verified=True,
                    pid=proc.pid,
                ),
                indent=2,
            )
        )
