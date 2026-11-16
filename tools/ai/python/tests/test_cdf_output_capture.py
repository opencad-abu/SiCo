"""Retained CDF diagnostics across native execution, chunk transfer and replay."""

import json
import os
from pathlib import Path

import pytest
from test_cdf_callbacks_native import native
from test_skill_output_capture import AI, run_native

from sico.core.contracts import ToolCall


@pytest.mark.parametrize("failure", [False, True])
def test_probe_capture_restores_ports_and_retains_outcome(tmp_path, failure):
    run_native(tmp_path, [
        *[f'load({json.dumps(str(AI / "skill" / name))})' for name in
          ("AI_circuit.il", "AI_circuit_task.il", "AI_cdf_probe.il")],
        'ocCalls=0 ocPo=outstring() ocWo=outstring() ocErr=outstring()',
        'procedure(ocProbe(ocEntry) ocCalls=ocCalls+1 ocCaptured=poport '
        'ocEntry[\'stage]="injected" printf("PRINT\\n") '
        'warn("FIRST\\n") warn("LAST\\n") '
        'fprintf(woport "WARNING_PORT\\n") fprintf(errport "ERROR_PORT\\n") '
        + ('error("UNEXPECTED_PROBE_FAILURE"))' if failure else 't)'),
        "putd('aiCdfProbeRun getd('ocProbe))",
        'let(((poport ocPo) (woport ocWo) (errport ocErr))',
        'warn("BEFORE_REQUEST\\n")',
        'ocResult=aiCdfProbe("cdf:test" "task:test" "request" '
        'list("work" "probe" "schematic") nil nil "cdf-probe:test")',
        'aiWriteTextFile("result.json" ocResult)',
        'unless(and(poport==ocPo woport==ocWo errport==ocErr '
        'openportp(ocPo) openportp(ocWo) openportp(ocErr) !openportp(ocCaptured) !getWarn()) '
        'error("Probe ports/warnings escaped capture"))',
        'unless(and(aiCdfProbeGet("cdf-probe:test")==ocResult '
        'aiCdfProbe("cdf:test" "task:test" "request" '
        'list("work" "probe" "schematic") nil nil "cdf-probe:unused")==ocResult ocCalls==1) '
        'error("Retained diagnostics changed or callbacks repeated"))',
        'printf("AFTER_REQUEST\\n"))',
        'aiWriteTextFile("outer-output" getOutstring(ocPo))',
        'aiWriteTextFile("outer-warning" getOutstring(ocWo))',
        'aiWriteTextFile("outer-error" getOutstring(ocErr))',
        'close(ocPo) close(ocWo) close(ocErr)',
    ])
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["ok"] is not failure
    assert result["output_capture"] == "skill_ports" and not result["output_truncated"]
    assert result["output"].startswith("PRINT\n*WARNING* FIRST\nWARNING_PORT\nERROR_PORT\n")
    assert result["output"].endswith("*WARNING* LAST\n")
    if failure:
        assert "UNEXPECTED_PROBE_FAILURE" in result["error"]
        assert "*Error* UNEXPECTED_PROBE_FAILURE" in result["output"]
    assert (tmp_path / "outer-output").read_text() == "AFTER_REQUEST\n"
    assert (tmp_path / "outer-warning").read_text() == "*WARNING* BEFORE_REQUEST\n"
    assert (tmp_path / "outer-error").read_text() == ""


@pytest.mark.parametrize("chunk", ["中文🙂", "\n\t\x01\\\""])
def test_probe_output_is_bounded_before_retention(tmp_path, chunk):
    literal = '"' + chunk.replace("\\", "\\\\").replace('"', '\\"').replace(
        "\n", "\\n").replace("\t", "\\t").replace("\x01", "\\001") + '"'
    run_native(tmp_path, [
        *[f'load({json.dumps(str(AI / "skill" / name))})' for name in
          ("AI_circuit.il", "AI_circuit_task.il", "AI_cdf_probe.il")],
        f'ocText={literal} for(ocIndex 1 14 ocText=strcat(ocText ocText))',
        'procedure(ocProbe(ocEntry) printf("%s" ocText) t)',
        "putd('aiCdfProbeRun getd('ocProbe))",
        'aiWriteTextFile("result.json" aiCdfProbe("cdf:test" "task:test" "request" '
        'list("work" "probe" "schematic") nil nil "cdf-probe:test"))',
        'aiWriteTextFile("retained.json" aiCdfProbeGet("cdf-probe:test"))',
    ])
    raw = (tmp_path / "result.json").read_bytes()
    result = json.loads(raw)
    assert result["ok"] and result["output_truncated"]
    assert result["output"] and (chunk * 16384).startswith(result["output"])
    assert len(result["output"].encode()) <= 65536 and len(raw) < 82000
    assert (tmp_path / "retained.json").read_bytes() == raw


@pytest.mark.skipif(os.environ.get("SICO_AI_RUN_CDF_TESTS") != "1",
                    reason="licensed isolated CDF acceptance")
def test_callback_output_survives_sico_chunks_failure_and_replay(native, tmp_path):
    skill, call, registry, context = native
    skill('progn('
          'procedure(cdfOutputCallback() cdfOutputCalls=cdfOutputCalls+1 '
          'printf("CDF_CALLBACK:%s\\n" cdfgData->w->value) '
          'printf("%s" cdfOutputLong) warn("CDF_WARNING\\n") warn("CDF_LAST\\n") '
          'fprintf(woport "CDF_WARNING_PORT\\n") fprintf(errport "CDF_ERROR_PORT\\n") '
          'when(cdfOutputFail error("CDF_CALLBACK_FAILURE")) t) '
          'cdfOutputCalls=0 cdfOutputFail=nil cdfOutputLong="宽度🙂" '
          'for(cdfOutputIndex 1 10 cdfOutputLong=strcat(cdfOutputLong cdfOutputLong)) '
          'cdfOutputPo=poport cdfOutputWo=woport cdfOutputErr=errport '
          'cdfOutputMaster=dbOpenCellViewByType("cdfDevices" "capture" "symbol" "schematicSymbol" "a") '
          'dbCreateRect(cdfOutputMaster list("device" "drawing") list(-0.5:-0.5 0.5:0.5)) '
          'dbSave(cdfOutputMaster) dbClose(cdfOutputMaster) '
          'cdfOutputBase=cdfCreateBaseCellCDF(ddGetObj("cdfDevices" "capture")) '
          'cdfCreateParam(cdfOutputBase ?name "w" ?prompt "Width" ?type "string" ?defValue "1u" '
          '?callback "cdfOutputCallback()") cdfSaveCDF(cdfOutputBase) t)')
    for failure in (False, True):
        skill("cdfOutputFail=" + ("t" if failure else "nil"))
        capture = call("inspect_cdf", dict(
            target=dict(library="cdfDevices", cell="capture", view="symbol")))
        key = "failure" if failure else "success"
        args = dict(cdf_ref=capture["cdf_ref"], task_ref="task:cdf", request_id=key,
                    target=dict(library="SicoTest", cell=key, view="schematic"),
                    parameters=dict(w="2u"), callback_order=["w"])
        outcome = registry.execute(ToolCall(key, "probe_cdf_parameters", args), context)
        assert outcome.status == ("tool_error" if failure else "ok"), outcome.record()
        result = outcome.data
        assert result["ok"] is not failure
        assert result["saved"] is not failure and result["readback_verified"] is not failure
        assert result["cdf_values_restored"] and result["cdf_metadata_unchanged"]
        assert result["output_capture"] == "skill_ports" and not result["output_truncated"]
        assert "宽度🙂" * 1024 in result["output"]
        for marker in ("CDF_CALLBACK:2u", "CDF_WARNING", "CDF_WARNING_PORT", "CDF_ERROR_PORT"):
            assert marker in result["output"]
        if failure:
            assert "CDF_CALLBACK_FAILURE" in result["error"]
            assert "*Error* CDF_CALLBACK_FAILURE" in result["output"]
        evidence = json.loads(Path(result["artifact"]["path"]).read_text())
        assert evidence["output"] == result["output"]
        assert registry.execute(ToolCall(key, "probe_cdf_parameters", args), context).data == result
        retained = registry.execute(ToolCall(key, "get_cdf_probe", dict(
            probe_ref=result["probe_ref"])), context)
        assert retained.data == result
        assert skill('and(poport==cdfOutputPo woport==cdfOutputWo errport==cdfOutputErr '
                     '!getWarn() cdfOutputCalls==' + ("2" if failure else "1") + ')') == "t"
    # The callback diagnostics travel in framed replies, not through CIW/log polling.
    for name in ("stdout.log", "CDS.log"):
        assert "CDF_CALLBACK:2u" not in (tmp_path / name).read_text()
        assert "*Error* CDF_CALLBACK_FAILURE" not in (tmp_path / name).read_text()
