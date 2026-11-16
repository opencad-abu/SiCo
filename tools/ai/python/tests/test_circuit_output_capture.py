"""Circuit diagnostics, native port lifetime and bounded SiCo replies."""

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from test_agent_bridge import connect_peer
from test_agent_project import bound
from test_skill_output_capture import AI, run_native

from sico.adapters.circuit import CircuitClient
from sico.core.contracts import BoundContext
from sico.transport.bridge_client import BridgeClient
from sico.transport.broker import skill_call_context
from sico.transport.circuit import encode_value
from sico.transport.framing import VERSION, strict_json
from sico.transport.instance_bridge import InstanceBridge
from sico.transport.relay import circuit_diagnostic

MODULES = ("AI_circuit.il", "AI_copilot_literals.il", "AI_copilot_targets.il", "AI_copilot_circuit.il")


def modules():
    return [f"load({json.dumps(str(AI / 'skill' / name))})" for name in MODULES]


def request(function, values=None, name="result"):
    fields = [name, "circuit_call", "target", function, encode_value([values or [], None, None])]
    return "sicoCircuitReply(nil list(" + " ".join(json.dumps(f) for f in fields) + "))"


@pytest.mark.parametrize("function", ["aiCrInspectTarget", "aiCreatePrepare",
    "aiCreateExecute", "aiCreateInspect", "aiCdfUpdatePrepare", "aiCdfUpdateApply",
    "aiLibraryPrepare", "aiLibraryCreate", "aiTemplateSymbolPreview", "aiTemplateSymbolCreate",
    "aiDrawSymbol", "aiTemplateSymbolInspect", "aiTemplateSymbolBinding",
    "aiConfigCreate", "aiConfigInspect", "aiCopilotDescribe", "aiCrListExtensions",
    "aiCrBindingGeometry", "aiPdkCapture", "aiPdkRead", "aiPdkDrop",
    "aiCopilotResolveResources", "aiProjectClaim", "aiPdkCdfInspect", "aiPdkCdfCase",
    "aiPdkPasDpt", "aiPdkSymbolWireProbe",
    "aiCopilotListTargets", "aiCopilotListLibraries", "aiCopilotListCells", "aiCopilotListViews"])
@pytest.mark.parametrize("failure", [False, True])
def test_request_output_restores_ports_and_keeps_failure_evidence(tmp_path, function, failure):
    result = json.dumps(json.dumps(dict(valid=True, circuit=dict(ok=False, code="business_failure"))))
    run_native(tmp_path, modules() + [
        'procedure(sicoSessionTarget(cid target) t)',
        'procedure(ocDispatch(source fn envelope @optional cid) '
        'aiCopilotEnterFunction(fn) ocCaptured=poport '
        'printf("PRINT\\n") warn("FIRST\\n") warn("LAST\\n") '
        'fprintf(woport "WARNING_PORT\\n") fprintf(errport "ERROR_PORT\\n") '
        + ('error("EXPECTED_FAILURE"))' if failure else result + ')'),
        "putd('aiCopilotDispatch getd('ocDispatch))",
        'ocPo=outstring() ocWo=outstring() ocErr=outstring()',
        'let(((poport ocPo) (woport ocWo) (errport ocErr))',
        'warn("BEFORE_REQUEST\\n")',
        'aiWriteTextFile("result.json" ' + request(function) + ')',
        'unless(and(poport==ocPo woport==ocWo errport==ocErr '
        'openportp(ocPo) openportp(ocWo) openportp(ocErr) !openportp(ocCaptured)) '
        'error("Ports not restored"))',
        'unless(!getWarn() error("Pending warning leaked"))',
        'printf("AFTER_REQUEST\\n")',
        'procedure(ocQuiet(source fn envelope @optional cid) '
        '"{\\"valid\\":true,\\"circuit\\":{\\"ok\\":true}}")',
        "putd('aiCopilotDispatch getd('ocQuiet))",
        'aiWriteTextFile("next.json" ' + request(function, name="next") + '))',
        'aiWriteTextFile("outer-output" getOutstring(ocPo))',
        'aiWriteTextFile("outer-warning" getOutstring(ocWo))',
        'aiWriteTextFile("outer-error" getOutstring(ocErr))',
        'close(ocPo) close(ocWo) close(ocErr)',
    ])
    reply = strict_json((tmp_path / "result.json").read_bytes())
    data = reply["diagnostic" if failure else "result"]
    assert reply["ok"] is not failure
    assert data["output_capture"] == "skill_ports" and not data["output_truncated"]
    assert data["output"].startswith("PRINT\n*WARNING* FIRST\nWARNING_PORT\nERROR_PORT\n")
    assert data["output"].endswith("*WARNING* LAST\n")
    if failure:
        assert "EXPECTED_FAILURE" in data["raw_error"]
        assert "*Error* EXPECTED_FAILURE" in data["output"]
        assert data["stage"] == ("describe" if function == "aiCopilotDescribe" else "invoke")
        assert data["operation_dispatched"] is (function in {
            "aiCreateExecute", "aiCdfUpdateApply", "aiLibraryCreate", "aiTemplateSymbolCreate",
            "aiDrawSymbol", "aiConfigCreate", "aiProjectClaim"})
    else:
        assert data["circuit"] == {"ok": False, "code": "business_failure"}
    assert (tmp_path / "outer-output").read_text() == "AFTER_REQUEST\n"
    assert (tmp_path / "outer-warning").read_text() == "*WARNING* BEFORE_REQUEST\n"
    assert (tmp_path / "outer-error").read_text() == ""
    assert strict_json((tmp_path / "next.json").read_bytes())["result"]["output"] == ""


def test_window_helpers_and_async_requests_retain_native_ports(tmp_path):
    run_native(tmp_path, modules() + [
        'procedure(sicoSessionTarget(cid target) t)',
        'procedure(ocWindow() ocDelayedPo=poport ocDelayedWo=woport ocDelayedErr=errport '
        'printf("WINDOW\\n") warn("WINDOW_WARNING\\n") t)',
        'procedure(ocDispatch(source fn envelope @optional cid) '
        'if(fn=="aiCreateExecute" then '
        'printf("CAPTURED\\n") warn("CAPTURED_WARNING\\n") '
        'aiCircuitNativeCall(\'ocWindow nil) printf("RESUMED\\n") '
        'else ocAsyncPo=poport ocAsyncWo=woport ocAsyncErr=errport printf("ASYNC\\n")) '
        '"{\\"valid\\":true,\\"circuit\\":{\\"ok\\":true}}")',
        "putd('aiCopilotDispatch getd('ocDispatch))",
        'ocPo=outstring() ocWo=outstring() ocErr=outstring()',
        'let(((poport ocPo) (woport ocWo) (errport ocErr))',
        'aiWriteTextFile("window.json" ' + request("aiCreateExecute") + ')',
        'unless(and(ocDelayedPo==ocPo ocDelayedWo==ocWo ocDelayedErr==ocErr) '
        'error("Window retained temporary ports"))',
        'fprintf(ocDelayedPo "LATER\\n") fprintf(ocDelayedWo "LATER_WARNING\\n") '
        'fprintf(ocDelayedErr "LATER_ERROR\\n")',
        'aiWriteTextFile("async.json" ' + request("aiSimRun") + ')',
        'unless(and(ocAsyncPo==ocPo ocAsyncWo==ocWo ocAsyncErr==ocErr) '
        'error("Async request redirected ports")))',
        'aiWriteTextFile("outer-output" getOutstring(ocPo))',
        'aiWriteTextFile("outer-warning" getOutstring(ocWo))',
        'aiWriteTextFile("outer-error" getOutstring(ocErr))',
        'close(ocPo) close(ocWo) close(ocErr)',
    ])
    window = strict_json((tmp_path / "window.json").read_bytes())["result"]
    assert window["output"] == "CAPTURED\n*WARNING* CAPTURED_WARNING\nRESUMED\n"
    assert "output_capture" not in strict_json((tmp_path / "async.json").read_bytes())["result"]
    assert (tmp_path / "outer-output").read_text() == "WINDOW\nLATER\nASYNC\n"
    assert (tmp_path / "outer-warning").read_text() == "*WARNING* WINDOW_WARNING\nLATER_WARNING\n"
    assert (tmp_path / "outer-error").read_text() == "LATER_ERROR\n"


def test_simulation_preflight_capture_keeps_ade_ports_native(tmp_path):
    skill = AI / "skill"
    modules = [
        f"load({json.dumps(str(skill / name))})"
        for name in ("AI_circuit.il", "AI_simulation_check.il", "AI_simulation_setup.il")
    ]
    run_native(tmp_path, modules + [
        'aiCircuitTasks=makeTable("probeTasks" nil)',
        'aiCircuitTasks["task:probe"]="foreground"',
        'procedure(aiRcReserve(key values) makeTable("probeRecord" nil))',
        'procedure(aiSimWork(entry) printf("WORK\\n") t)',
        'procedure(aiSimFileCheck(entry models) printf("FILES\\n") t)',
        'procedure(aiCircuitSessions(target) nil)',
        'procedure(aiCircuitCvMatches(cv target) nil)',
        'procedure(aiSimSources(entry) printf("SOURCE_CHECK\\n") error("SOURCE_DIRTY"))',
        'procedure(aiSimGlobalValues(payload) nil)',
        'procedure(aiSimExpected(payload) nil)',
        'procedure(aiSimConfigSummary(entry) nil)',
        'procedure(hiGetWindowList(mode) nil)',
        'probePayload=list(list("ProbeLib" "probe" "schematic") nil "27" '
        'list(list("t1" list("ProbeLib" "probe" "schematic") "spectre" nil nil nil)) nil nil)',
        'ocPo=outstring() ocWo=outstring() ocErr=outstring()',
        'let(((poport ocPo) (woport ocWo) (errport ocErr))',
        '  probeResult=errset(aiSimCreate("task:probe" "req" "digest" probePayload nil) t)',
        '  unless(probeResult error("aiSimCreate did not return a receipt")))',
        'aiWriteTextFile("reply.json" car(probeResult))',
        'aiWriteTextFile("outer-output" getOutstring(ocPo))',
        'aiWriteTextFile("outer-warning" getOutstring(ocWo))',
        'aiWriteTextFile("outer-error" getOutstring(ocErr))',
        'close(ocPo) close(ocWo) close(ocErr)',
    ])
    reply = json.loads((tmp_path / "reply.json").read_text())
    assert reply["ok"] is False and reply["stage"] == "preflight"
    assert reply["output_capture"] == "skill_ports" and not reply["output_truncated"]
    assert reply["output"].startswith("WORK\nFILES\nSOURCE_CHECK\n*Error* SOURCE_DIRTY")
    assert "SOURCE_DIRTY" in reply["error"]
    assert (tmp_path / "outer-output").read_text() == ""
    assert (tmp_path / "outer-warning").read_text() == ""
    assert (tmp_path / "outer-error").read_text() == ""


def test_simulation_source_status_allows_warning_markers_only(tmp_path):
    source = AI / "skill" / "AI_simulation_check.il"
    run_native(tmp_path, [
        f"load({json.dumps(str(source))})",
        "procedure(schExtractStatus(cv) probeStatus)",
        "procedure(geHasMarker(cv owner type) probeErrorMarker)",
        'probeStatus="clean" probeErrorMarker=nil',
        'unless(aiSimSourceReadyP("cv") error("clean source rejected"))',
        'probeStatus="dirty" probeErrorMarker=nil',
        'unless(aiSimSourceReadyP("cv") error("warning-only source rejected"))',
        'probeStatus="dirty" probeErrorMarker=t',
        'when(aiSimSourceReadyP("cv") error("error-marker source accepted"))',
        'probeStatus="obsolete" probeErrorMarker=nil',
        'when(aiSimSourceReadyP("cv") error("obsolete source accepted"))',
    ])


def test_binding_geometry_failure_returns_a_structured_receipt(tmp_path):
    source = AI / "skill" / "AI_circuit_create_check.il"
    run_native(tmp_path, [
        f"load({json.dumps(str(AI / 'skill' / 'AI_circuit.il'))})",
        f"load({json.dumps(str(source))})",
        'procedure(aiCrErrorText() "body unavailable")',
        'checkResult=errset(aiCrBindingGeometry("NoSuchLib" "NoSuchCell" "symbol" "/no/such/lib") t)',
        'unless(checkResult error("binding geometry raised instead of returning a receipt"))',
        "aiWriteTextFile(\"geometry.json\" car(checkResult))",
    ])
    reply = json.loads((tmp_path / "geometry.json").read_text())
    assert reply["ok"] is False and reply["code"] == "pdk_body_unavailable"


def test_binding_geometry_success_returns_the_shared_receipt(tmp_path):
    source = AI / "skill" / "AI_circuit_create_check.il"
    run_native(tmp_path, [
        f"load({json.dumps(str(AI / 'skill' / 'AI_circuit.il'))})",
        f"load({json.dumps(str(source))})",
        "procedure(aiCrBodyData(acbLib acbCell acbView acbPath) "
        "progn(bd=makeTable(\"bd\" nil) "
        "bd['occupied_bbox]=list(list(0 0) list(10 5)) "
        "bd['annotation_bbox]=list(list(0 0) list(10 5)) "
        "bd['pin_centers]=list(list(\"A\" list(0 0))) "
        "bd['signature]=\"sig\" bd))",
        'geometry=car(errset(aiCrBindingGeometry("L" "C" "symbol" "/p") t))',
        'unless(geometry error("binding geometry raised instead of returning a receipt"))',
        "aiWriteTextFile(\"geometry.json\" geometry)",
    ])
    reply = json.loads((tmp_path / "geometry.json").read_text())
    assert reply["ok"] is True
    assert reply["occupied_bbox"] == [[0, 0], [10, 5]]
    assert reply["pin_centers"] == [["A", [0, 0]]]


def test_master_evidence_invalid_targets_return_a_structured_receipt(tmp_path):
    run_native(tmp_path, [
        f"load({json.dumps(str(AI / 'skill' / 'AI_inspect_values.il'))})",
        f"load({json.dumps(str(AI / 'skill' / 'AI_inspect.il'))})",
        'checkResult=errset(aiInspectMasterEvidence(list("not-a-triple") 4) t)',
        'unless(checkResult error("master evidence raised instead of returning a receipt"))',
        "aiWriteTextFile(\"inspect.json\" car(checkResult))",
    ])
    reply = json.loads((tmp_path / "inspect.json").read_text())
    assert reply["ok"] is False and reply["error"]["code"] == "invalid_targets"


@pytest.mark.parametrize("chunk", ["\u4e2d\u6587\U0001f642", "\n\t\x01\\\""])
def test_output_budget_preserves_large_business_result(tmp_path, chunk):
    literal = '"' + chunk.replace("\\", "\\\\").replace('"', '\\"').replace(
        "\n", "\\n").replace("\t", "\\t").replace("\x01", "\\001") + '"'
    run_native(tmp_path, modules() + [
        'procedure(sicoSessionTarget(cid target) t)',
        f'ocLong={literal}',
        'for(ocIndex 1 14 ocLong=strcat(ocLong ocLong))',
        'ocBusiness="x" for(ocIndex 1 17 ocBusiness=strcat(ocBusiness ocBusiness))',
        'procedure(ocDispatch(source fn envelope @optional cid) printf("%s" ocLong) '
        'strcat("{\\"valid\\":true,\\"circuit\\":{\\"ok\\":true,\\"data\\":\\"" '
        'ocBusiness "\\"}}"))',
        "putd('aiCopilotDispatch getd('ocDispatch))",
        'aiWriteTextFile("result.json" ' + request("aiCreateInspect") + ')',
    ])
    raw = (tmp_path / "result.json").read_bytes()
    reply = strict_json(raw)
    assert reply["ok"] and reply["result"]["circuit"]["data"] == "x" * 131072
    assert reply["result"]["output_truncated"]
    output = reply["result"]["output"]
    assert output and (chunk * 16384).startswith(output)
    assert len(output.encode()) <= 65536 and len(raw) < 262144


def test_captured_success_survives_bridge_and_business_adapter(tmp_path):
    original = bound(tmp_path)
    context = BoundContext(original.instance_id, original.generation, original.target_id,
                           {**original.snapshot, "capabilities": ["circuit_call"]})
    with InstanceBridge(tmp_path, context) as bridge:
        peer = connect_peer(bridge.broker, context)
        try:
            with BridgeClient.discover(tmp_path, context) as client, ThreadPoolExecutor(1) as pool:
                client.register_session("session", context)

                def read():
                    with skill_call_context(session_id="session"):
                        return CircuitClient(client, context, tmp_path).call(
                            "eval_skill_native", {"code": 'aiCreateInspect("created:test")'})

                pending = pool.submit(read)
                frame = peer.receive()
                peer.send(dict(protocol=VERSION, kind="response", generation=context.generation,
                               id=frame["id"], ok=True, result=dict(
                                   valid=True, circuit={"ok": False, "stage": "check"},
                                   output="*WARNING* CHECK\n", output_truncated=False,
                                   output_capture="skill_ports")))
                ok, detail = pending.result(5)
                assert ok
                assert json.loads(detail["value"]) == dict(
                    ok=False, stage="check", output="*WARNING* CHECK\n",
                    output_truncated=False, output_capture="skill_ports")
        finally:
            peer.close()


def test_legacy_circuit_replies_remain_usable(tmp_path):
    broker = SimpleNamespace(circuit_call=lambda *_: {"valid": True, "circuit": {"ok": True}})
    assert CircuitClient(broker, bound(tmp_path), tmp_path).send("aiCreateInspect", []) == {"ok": True}


def test_diagnostic_output_limit_reports_additional_truncation():
    text = "\u4e2d\u6587" * 20000
    result = circuit_diagnostic({"diagnostic": dict(
        output=text, output_truncated=False, output_capture="skill_ports")})
    assert text.startswith(result["output"]) and len(result["output"].encode()) <= 65536
    assert result["output_truncated"] and result["diagnostic_truncated"]
