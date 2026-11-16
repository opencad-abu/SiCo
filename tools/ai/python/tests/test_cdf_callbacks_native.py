"""Isolated Virtuoso acceptance for built-in callbacks, with no user library writes."""

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest

from sico.adapters.virtuoso import context_tools
from sico.adapters.circuit import CircuitClient
from sico.core.contracts import ToolCall
from sico.interpreter import cad_python
from sico.storage.journal import SessionJournal
from sico.transport.broker import ContextBroker

CAD = Path(__file__).resolve().parents[3]
IC = Path("/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/virtuoso")
LIBS = IC.parents[1] / "etc/cdslib"
pytestmark = pytest.mark.skipif(os.environ.get("CAD_AI_RUN_CDF_TESTS") != "1",
                                reason="licensed isolated CDF acceptance")


@pytest.fixture
def native(tmp_path):
    mappings = [f"DEFINE basic {LIBS}/basic", f"DEFINE analogLib {LIBS}/artist/analogLib"]
    pdk = os.environ.get("CAD_AI_SMIC28_PDK")
    if pdk:
        mappings.append(f"DEFINE smic28hkmg {pdk}/smic28hkmg")
    (tmp_path / "cds.lib").write_text("\n".join(mappings) + "\n")
    with ContextBroker(15, operation_timeout=20) as broker, SessionJournal(
        tmp_path, "cdf-test"
    ) as journal:
        config = tmp_path / "connection.json"
        config.write_text(json.dumps(dict(host=broker.address[0], port=broker.address[1], token=broker.token)))
        config.chmod(0o600)
        command = shlex.join([cad_python(), "-s", str(CAD / "sico/python/sico-entry"),
                              "relay", "--connection-file", str(config)])
        stop = tmp_path / "stop"
        source = tmp_path / "startup.il"
        source.write_text("\n".join([
            f'load({json.dumps(str(CAD / "sico/skill++/SICO.ils"))})',
            f'ddCreateLib("cdfWork" {json.dumps(str(tmp_path / "cdfWork"))})',
            f'ddCreateLib("cdfDevices" {json.dumps(str(tmp_path / "cdfDevices"))})',
            'aiWriteTextFile("cdfDevices/libInit.il" "t\\n")',
            'cdfTestSource=makeTable("cdfTestSource" nil)',
            f'sicoConnect({json.dumps(command)} "cdf-test" "generation" nil cdfTestSource)',
            'cdfTestTicks=0',
            'procedure(cdfTestExit() cdfTestTicks=cdfTestTicks+1 '
            f'if(or(isFile({json.dumps(str(stop))}) cdfTestTicks>3000) then exit() '
            'else hiRegTimer("cdfTestExit()" 1)))',
            'hiRegTimer("cdfTestExit()" 1)',
        ]) + "\n")
        env = {k: v for k, v in os.environ.items()
               if k not in {"PYTHONHOME", "PYTHONPATH", "LD_PRELOAD", "LD_LIBRARY_PATH"}}
        env["SICO_ROOT"] = str(CAD / "ai")
        with (tmp_path / "stdout.log").open("w") as output:
            process = subprocess.Popen([str(IC), "-nographE", "-nocdsinit", "-cdslib",
                str(tmp_path / "cds.lib"), "-restore", str(source), "-log", str(tmp_path / "CDS.log")],
                cwd=tmp_path, env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
        try:
            context = broker.context("cdf-test", timeout=25)
            registry = context_tools(broker, journal)

            def skill(code):
                reply = broker.assistant_call(context, "eval_skill_native", code)
                assert reply["ok"], json.dumps(reply, indent=2)
                detail = reply.get("result", reply)
                assert detail["ok"], detail
                return detail.get("value")

            def call(name, args):
                result = registry.execute(ToolCall("cdf-test", name, args), context)
                assert result.status == "ok", json.dumps(result.record(), indent=2)
                return result.data

            skill.client = CircuitClient(broker, context, tmp_path)
            call("begin_circuit_task", dict(task_id="cdf", presentation="background", choice_confirmed=True))
            yield skill, call, registry, context
        finally:
            stop.touch()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def test_installed_parser_and_smic_create_update(native, tmp_path):
    if not os.environ.get("CAD_AI_SMIC28_PDK"):
        pytest.skip("explicit licensed SMIC28 PDK root required")
    skill, call, registry, context = native
    (tmp_path / "device-cdf.json").write_text(json.dumps({cell: call("inspect_cdf", dict(
        target=dict(library="smic28hkmg", cell=cell, view="symbol"), limit=64))
        for cell in ("rnpo_2t_ckt", "mom_2t_ckt")}, indent=2))
    skill('progn(procedure(cdfParserTest(x) x) '
          'unless(equal(cadr(aiCdfCallbackParse("cdfParserTest( \'w )")) list(\'w)) error("parser")) '
          'unless(null(aiCdfCallbackParse("nil")) error("literal nil callback was not a no-op")) '
          'foreach(txt list("nil exit()" "cdfParserTest(1) exit()" '
          '"eval(1)" "cdfParserTest(exit())") '
          'when(errset(aiCdfCallbackParse(txt) nil) error("unsafe callback accepted"))) t)')
    for cell, rows in [
        ("p09_ckt", [["l", "string", "60n"], ["fingers", "string", "2"], ["w", "string", "1u"], ["m", "string", "1"]]),
        ("n09_ckt", [["l", "string", "60n"], ["fingers", "string", "2"], ["w", "string", "1u"], ["m", "string", "1"]]),
        ("rnpo_2t_ckt", [["calculatedParam", "string", "Length"], ["r", "string", "25K"]]),
        ("mom_2t_ckt", [["col", "string", "7"], ["row", "string", "7"]]),
    ]:
        from cadai.circuit_create import skill_literal
        evidence = json.loads(skill('aiCrListExtensions("smic28hkmg" ' + json.dumps(cell) +
            ' "symbol" ddGetObjReadPath(ddGetObj("smic28hkmg")))'))
        assert evidence["items"] == [
            ["builtin:cdf-callbacks:v1", "20261022.ai.cdf.callbacks.v5", True]
        ], evidence
        result = skill('let((cdfTestCv cdfTestMaster cdfTestInst cdfTestBefore) '
            f'cdfTestMaster=dbOpenCellViewByType("smic28hkmg" "{cell}" "symbol" "" "r") '
            f'cdfTestBefore=aiCdfSourceState(cdfGetCellCDF(ddGetObj("smic28hkmg" "{cell}"))) '
            f'cdfTestCv=dbOpenCellViewByType("cdfWork" "test_{cell}" "schematic" "schematic" "a") '
            f'cdfTestInst=aiCdfCallbackCreate(cdfTestCv cdfTestMaster "M0" 0:0 "R0" {skill_literal(rows)}) '
            f'unless(equal(cdfTestBefore aiCdfSourceState(cdfGetCellCDF(ddGetObj("smic28hkmg" "{cell}")))) error("shared CDF changed")) '
            'dbSave(cdfTestCv) dbClose(cdfTestCv) dbClose(cdfTestMaster) t)')
        assert result == "t"
    target = dict(library="cdfWork", cell="test_p09_ckt", view="schematic")
    captured = call("inspect_cdf", dict(target=target, instance="M0", parameters=["w", "fingers", "fw"]))
    prepared = call("prepare_cdf_update", dict(cdf_ref=captured["cdf_ref"], task_ref="task:cdf",
        request_id="change", parameters=dict(fingers="4", w="2u"), callback_order=["fingers", "w"]))
    call("preflight_circuit_project", dict(presentation="background",
        targets=[dict(target, oa_view_type="schematic", intent="edit")],
        libraries=[dict(library="cdfWork", expected_path=str(tmp_path / "cdfWork"))],
        directories=[dict(path=str(tmp_path), purpose="project", min_free_bytes=0, min_free_inodes=0)], model_files=[]))
    args = dict(operation="apply_cdf_update", arguments=dict(prepare_ref=prepared["prepare_ref"], request_id="update"))
    result = call("execute_circuit_operation", args)
    assert result["receipt"]["saved"] and result["receipt"]["readback_verified"], result
    assert result["receipt"]["cdf_values_restored"] and result["receipt"]["cdf_metadata_unchanged"]
    assert call("execute_circuit_operation", args)["receipt"] == result["receipt"]
    actual = call("inspect_cdf", dict(target=target, instance="M0", parameters=["w", "fingers", "fw"]))
    values = {p["name"]: p["current"]["value"] for p in actual["parameters"]}
    assert values == dict(w="2u", fingers="4", fw="500n"), values
    capture = call("inspect_cdf", dict(target=dict(library="smic28hkmg", cell="p09_ckt", view="symbol")))
    probe = call("probe_cdf_parameters", dict(cdf_ref=capture["cdf_ref"], task_ref="task:cdf",
        request_id="builtin-probe", target=dict(library="SicoTest", cell="diagnostic", view="schematic"),
        parameters=dict(l="60n", fingers="2", w="1000n", m="1"), callback_order=["l", "fingers", "w", "m"]))
    assert probe["saved"] and probe["readback_verified"] and probe["all_requested_match"], probe
    assert probe["cdf_values_restored"] and probe["cdf_metadata_unchanged"], probe
    assert probe["trace_coverage"] == "builtin_parameter_checkpoints"
    (tmp_path / "acceptance.json").write_text(json.dumps(dict(update=result, values=values), indent=2))


def test_selective_callbacks_ignore_gui_and_restore_failures(native, tmp_path):
    skill, call, registry, context = native
    skill('progn('
        'procedure(cdfTestChanged() cdfTestCalls=cdfTestCalls+1 '
        'printf("CDF_CALLBACK:%s\\n" cdfgData->w->value) '
        'warn("CDF_WARNING\\n") warn("CDF_BUFFERED\\n") '
        'fprintf(errport "CDF_ERROR_PORT\\n") '
        'when(cdfTestFail error("injected callback failure")) '
        'when(cdfTestOutside dbReplaceProp(cdfTestHeld "unexpected" "string" "changed")) '
        'when(cdfTestNormalize cdfgData->w->value="3u") t) '
        'procedure(cdfTestUntouched() error("unmodified parameter callback executed")) '
        'procedure(cdfTestGui() error("GUI condition executed")) '
        'cdfTestCalls=0 cdfTestFail=nil cdfTestOutside=nil cdfTestNormalize=nil '
        'cdfTestPo=poport cdfTestWo=woport cdfTestErr=errport '
        'cdfTestMaster=dbOpenCellViewByType("cdfDevices" "mos" "symbol" "schematicSymbol" "a") '
        'dbCreateRect(cdfTestMaster list("device" "drawing") list(-0.5:-0.5 0.5:0.5)) '
        'dbSave(cdfTestMaster) dbClose(cdfTestMaster) '
        'cdfTestBase=cdfCreateBaseCellCDF(ddGetObj("cdfDevices" "mos")) '
        'cdfCreateParam(cdfTestBase ?name "w" ?prompt "Width" ?type "string" ?defValue "1u" '
        '?editable "nil" ?display "cdfTestGui()" ?callback "cdfTestChanged()") '
        'cdfCreateParam(cdfTestBase ?name "untouched" ?prompt "Untouched" ?type "string" ?defValue "1" '
        '?editable "cdfTestGui()" ?callback "cdfTestUntouched()") '
        'cdfSaveCDF(cdfTestBase) '
        'cdfTestMaster=dbOpenCellViewByType("cdfDevices" "mos" "symbol" "" "r") '
        'unless(cdfFindParamByName(cdfGetCellCDF(ddGetObj("cdfDevices" "mos")) "w") error("fixture CDF missing")) '
        'cdfTestHeld=dbOpenCellViewByType("cdfWork" "controlled@sheet001" "schematic" "schematic" "a") '
        'dbCreateInst(cdfTestHeld cdfTestMaster "M0" 0:0 "R0") '
        'dbCreateInst(cdfTestHeld cdfTestMaster "M1" 2:0 "R0") '
        'dbSave(cdfTestHeld) dbClose(cdfTestMaster) t)')
    target = dict(library="cdfWork", cell="controlled@sheet001", view="schematic")

    def prepare(key, value):
        capture = call("inspect_cdf", dict(target=target, instance="M0"))
        return call("prepare_cdf_update", dict(cdf_ref=capture["cdf_ref"], task_ref="task:cdf",
            request_id=key, parameters=dict(w=value), callback_order=["w"]))

    def execute(prepared, key):
        return registry.execute(ToolCall(key, "execute_circuit_operation", dict(
            operation="apply_cdf_update", arguments=dict(prepare_ref=prepared["prepare_ref"],
                                                         request_id=key))), context)

    def preflight():
        call("preflight_circuit_project", dict(presentation="background",
            targets=[dict(target, oa_view_type="schematic", intent="edit")],
            libraries=[dict(library="cdfWork", expected_path=str(tmp_path / "cdfWork"))],
            directories=[dict(path=str(tmp_path), purpose="project", min_free_bytes=0,
                              min_free_inodes=0)], model_files=[]))

    prepared = prepare("success", "2u")
    assert prepared["output_capture"] == "skill_ports"
    assert skill("cdfTestCalls") == "0"
    refused = json.loads(skill('aiCdfUpdateApply(' + json.dumps(prepared["prepare_ref"]) + ' "unguarded")'))
    assert refused["code"] == "cdf_update_claim_required"
    preflight()
    success = execute(prepared, "success")
    assert success.status == "ok", success
    captured = success.data["last_response"]
    assert captured["output_capture"] == "skill_ports" and not captured["output_truncated"]
    assert all(marker in captured["output"] for marker in
               ("CDF_CALLBACK:2u", "CDF_WARNING", "CDF_ERROR_PORT"))
    assert call("get_circuit_operation", dict(operation="apply_cdf_update", request_id="success",
                refresh=False))["last_response"] == captured
    assert skill("cdfTestCalls") == "1"
    assert execute(prepared, "success").data["receipt"] == success.data["receipt"]
    assert skill("cdfTestCalls") == "1"
    assert skill('cdfGetInstCDF(dbFindAnyInstByName(cdfTestHeld "M1"))->w->value') == "1u"
    # Procedure replacement between prepare and apply must stop before callbacks.
    stale = prepare("stale", "4u")
    skill("progn(cdfTestOriginal=getd('cdfTestChanged) procedure(cdfTestChanged() error(\"replaced\")) t)")
    assert execute(stale, "stale").status != "ok"
    assert skill("cdfTestCalls") == "1"
    skill("putd('cdfTestChanged cdfTestOriginal)")
    evidence = dict(success=success.data)
    for key, control in [("throw", "cdfTestFail"), ("mismatch", "cdfTestNormalize"),
                         ("outside", "cdfTestOutside")]:
        prepared = prepare(key, "4u")
        preflight()
        skill(control + "=t")
        failed = execute(prepared, key)
        skill(control + "=nil")
        assert failed.status == "operation_failed", failed
        receipt = failed.data["receipt"]
        output = failed.data["last_response"]["output"]
        assert "CDF_CALLBACK:4u" in output and "CDF_WARNING" in output
        if key == "throw":
            assert "injected callback failure" in output
        assert not receipt["saved"] and receipt["cdf_values_restored"] and receipt["cdf_metadata_unchanged"], receipt
        assert receipt["instance_properties_restored"] is (key != "outside"), receipt
        assert skill('cdfGetInstCDF(dbFindAnyInstByName(cdfTestHeld "M0"))->w->value') == "2u"
        count = skill("cdfTestCalls")
        assert execute(prepared, key).data["receipt"] == receipt
        assert skill("cdfTestCalls") == count
        evidence[key] = failed.data
        # Fixture owns this view. Clear only the deliberate outside-scope mutation.
        if key == "outside":
            skill('dbDeletePropByName(cdfTestHeld "unexpected")')
        skill("dbSave(cdfTestHeld)")
        assert skill("and(poport==cdfTestPo woport==cdfTestWo errport==cdfTestErr "
                     "openportp(cdfTestPo) openportp(cdfTestWo) openportp(cdfTestErr))") == "t"
    (tmp_path / "failure-acceptance.json").write_text(json.dumps(evidence, indent=2))
    assert skill('let((cdfPrepared cdfCount cdfNew cdfMaster cdfRows) '
        'cdfMaster=dbFindAnyInstByName(cdfTestHeld "M0")~>master '
        'cdfRows=list(list("w" "string" "2u")) cdfCount=cdfTestCalls '
        'cdfPrepared=aiCdfCallbackApply(nil cdfMaster cdfRows nil nil nil nil nil t) '
        'unless(cdfTestCalls==cdfCount+1 error("callback evaluation count")) '
        'cdfNew=dbOpenCellViewByType("cdfWork" "staged_commit" "schematic" "schematic" "a") '
        'aiCdfCallbackApply(nil cdfMaster cdfRows cdfNew "M0" 0:0 "R0" nil nil cdfPrepared) '
        'unless(cdfTestCalls==cdfCount+1 error("callback repeated during commit")) '
        'dbSave(cdfNew) dbClose(cdfNew) t)') == "t"


def test_reconnected_source_can_recover_task_and_inspect(native, tmp_path):
    skill, call, registry, context = native
    skill('progn(cdfOldSource=aiStudioTasks["task:cdf"] '
          'aiStudioTasks["task:cdf"]=makeTable("previousSource" nil) t)')
    args = dict(task_ref="task:cdf", request_id="old-target",
                target=dict(library="cdfWork", cell="retained", view="schematic"))
    inspected_only = registry.execute(ToolCall("old", "inspect_circuit_target", args), context)
    assert inspected_only.status == "ok" and inspected_only.data["ok"], inspected_only.record()
    assert inspected_only.data["content_state"] == "absent"
    assert skill('!aiCrTargets["circuit-target:old-target"]["task"]') == "t"
    assert skill('eq(aiStudioTasks["task:cdf"] cdfOldSource)') == "nil"
    reused = registry.execute(ToolCall("old-task", "begin_circuit_task", dict(
        task_id="cdf", presentation="background", choice_confirmed=True)), context)
    assert reused.data["code"] == "task_source_mismatch", reused.record()
    call("begin_circuit_task", dict(task_id="recovered", presentation="background", choice_confirmed=True))
    inspected = call("inspect_circuit_target", {**args, "task_ref": "task:recovered", "request_id": "new-target"})
    assert inspected["ok"] and inspected["content_state"] == "absent", inspected


@pytest.mark.parametrize("design", ["inverter", "ldo", "ldo_conflict"])
def test_smic_binding_prepare_journalled_creation(native, tmp_path, design):
    if not os.environ.get("CAD_AI_SMIC28_PDK"):
        pytest.skip("explicit licensed SMIC28 PDK root required")
    from cadai.pdk_bridge import PdkBridge
    from cadai.pdk_session import PdkSession
    from cadai.pdk_binding import PdkBindings
    from cadai.circuit_generic import call_generic
    from cadai.circuit_create import call_create
    from cadai.project_tools import call_project
    from cadai.mcp import _TOOLS_BY_NAME

    skill, call, registry, context = native
    client = skill.client
    # Test only: scope live discovery to two selected devices, independently of
    # the workspace-wide PDK collection acceptance. All captures are real SKILL.
    session = PdkSession(bridge=PdkBridge(client))
    bindings = PdkBindings(session, client)
    records = []
    devices = [("P", "p09_ckt"), ("N", "n09_ckt")]
    if design != "inverter":
        devices += [("R", "rhrpo_2t_ckt"), ("C", "mom_2t_ckt")]
    for key, cell in devices:
        found = session.call("search_pdk_devices", dict(library="smic28hkmg", cell=cell))
        query = dict(snapshot_ref=found["context"]["snapshot_ref"], device_ref=found["items"][0]["device_ref"])
        query.update(sections=["geometry"], page_size=64)
        figures, device = [], None
        while True:
            page = session.call("get_pdk_device", {**query, "tier": "all"})
            device = page["device"]
            figures.extend(device["geometry"]["items"])
            if page["page"]["next_cursor"] is None:
                break
            query["cursor"] = page["page"]["next_cursor"]
        escapes = dict(G="left", B="right", D="down" if key == "P" else "up",
                       S="up" if key == "P" else "down")
        names = ["l", "fingers", "w", "m"]
        if key == "R":
            names, escapes = ["calculatedParam", "r"], dict(PLUS="up", MINUS="down")
        elif key == "C":
            names, escapes = ["col", "row"], dict(PLUS="up", MINUS="down")
        elif design == "ldo_conflict":
            names = ["w", "l", "fw", "fingers", "m"]
        records.append(bindings.bind(dict(snapshot_ref=query["snapshot_ref"], device_ref=query["device_ref"],
            revision=device["revision"], master_id=key, kind="device", project_ref="test:cdf",
            project_policy_ref="test:cdf-physical-parameters",
            writable_parameters=[dict(name=n, policy="cdf_callback", evidence="isolated physical sizing test")
                                 for n in names],
            callback_order=names,
            **(dict(netlist_terminal_map=dict(zip(
                    ["PLUS", "MINUS"], ["in", "out"] if key == "R" else ["p1", "m1"])))
               if key in {"R", "C"} else {}),
            pin_escapes=[dict(figure_ref=f["figure_ref"], escape=escapes[f["terminal"]]) for f in figures])))
    assert all(r["master"]["callbacks"]["extension_ref"] == "builtin:cdf-callbacks:v1" for r in records)
    target = dict(library="cdfWork", cell="inverter", view="schematic")
    request = dict(binding_refs=[r["binding_ref"] for r in records], spec=dict(
        schema="cad.circuit.spec.v1", kind="circuit", target=target,
        instances=[dict(id=key, name=key, master=key, role="device",
            parameters=dict(l="60n", fingers="2", w="1000n", m="1"),
            connections=dict(G="A", D="Y", S=supply, B=supply), unconnected={})
            for key, supply in [("P", "VDD"), ("N", "VSS")]],
        nets=[dict(name=n, scope="local") for n in ["A", "Y", "VDD", "VSS"]],
        ports=[dict(name=n, direction=d, net=n) for n, d in
               [("A", "input"), ("Y", "output"), ("VDD", "inputOutput"), ("VSS", "inputOutput")]]))
    if design != "inverter":
        target["cell"] = design
        rows = [
            ("MP0", "P", "30n", "15", "30u", ["VOUT", "VG", "VIN", "VIN"]),
            ("M1", "N", "100n", "2", "4u", ["ND1", "FB", "TAIL", "VSS"]),
            ("M2", "N", "100n", "2", "4u", ["VG", "VREF", "TAIL", "VSS"]),
            ("M3", "P", "100n", "2", "4u", ["ND1", "ND1", "VIN", "VIN"]),
            ("M4", "P", "100n", "2", "4u", ["VG", "ND1", "VIN", "VIN"]),
            ("M5", "N", "200n", "1", "2u", ["TAIL", "NBIAS", "VSS", "VSS"]),
            ("M6", "N", "200n", "1", "2u", ["NBIAS", "NBIAS", "VSS", "VSS"]),
        ]
        instances = [dict(id=n, name=n, master=m, role="device", unconnected={},
            parameters=dict(l=l, fingers=nf, w=w, m="1",
                            **(dict(fw="1u" if n in {"M5", "M6"} else "2u") if design == "ldo_conflict" else {})),
            connections=dict(zip(["D", "G", "S", "B"], nets))) for n, m, l, nf, w, nets in rows]
        for n, m, params, plus, minus in [
            ("R0", "R", dict(calculatedParam="Length", r="25k"), "VIN", "NBIAS"),
            ("R1", "R", dict(calculatedParam="Length", r="40k"), "VOUT", "FB"),
            ("R2", "R", dict(calculatedParam="Length", r="40k"), "FB", "VSS"),
            ("CC", "C", dict(col="7", row="7"), "VG", "VOUT"),
            ("CL", "C", dict(col="11", row="11"), "VOUT", "VSS"),
        ]:
            instances.append(dict(id=n, name=n, master=m, role="device", unconnected={},
                                  parameters=params, connections=dict(PLUS=plus, MINUS=minus)))
        request["spec"].update(instances=instances,
            nets=[dict(name=n, scope="local") for n in ["VIN", "VREF", "VOUT", "VSS", "VG", "ND1", "TAIL", "NBIAS", "FB"]],
            ports=[dict(name=n, direction=d, net=n) for n, d in
                   [("VIN", "input"), ("VREF", "input"), ("VOUT", "output"), ("VSS", "inputOutput")]])
    preview = call_generic("preview_circuit_spec", request, pdk_bindings=bindings)
    request.update(preview_digest=preview["preview_digest"], layout=dict(
        instances={"P": dict(xy=[0, 4], orientation="R0"), "N": dict(xy=[0, -4], orientation="R0")},
        ports={"A": dict(xy=[-6, 0], escape="right"), "Y": dict(xy=[6, 0], escape="left"),
               "VDD": dict(xy=[-6, 6], escape="right"), "VSS": dict(xy=[-6, 5], escape="right")},
        anchor_choices={}, grid=0.0625, stub_length=0.5, clearance=0,
        routing=dict(mode="stub", decision=dict(source="user", chosen="stub"))))
    if design != "inverter":
        request["layout"].update(clearance=0.25,
            instances={n: dict(xy=xy, orientation="R0") for n, xy in dict(
                CC=[9, 6], CL=[15, 0], M1=[0, 0], M2=[3, 0], M3=[0, 3], M4=[3, 3],
                M5=[6, 0], M6=[6, 3], MP0=[12, 6], R0=[9, 3], R1=[15, 6], R2=[15, 3]).items()},
            ports={n: dict(xy=xy, escape="right" if n == "VOUT" else "left") for n, xy in
                   dict(VIN=[-1, 0], VREF=[-1, 3], VOUT=[16.5, 3], VSS=[-1, 6]).items()})
    geometry = call_generic("preview_circuit_geometry", request, pdk_bindings=bindings)
    (tmp_path / "geometry.json").write_text(json.dumps(geometry, indent=2))
    assert geometry["plan"]["geometry_checks_passed"], json.dumps(geometry["plan"].get("issues"), indent=2)
    request.update(task_ref="task:cdf", request_id="inv-prepare", geometry_plan_digest=geometry["geometry_plan_digest"])
    ok, prepared = call_create("prepare_circuit_creation", request, client, tmp_path, bindings)
    assert ok, prepared
    requirements = dict(presentation="background", targets=[dict(target, oa_view_type="schematic", intent="create")],
        libraries=[dict(library="cdfWork", expected_path=str(tmp_path / "cdfWork"))],
        directories=[dict(path=str(tmp_path), purpose="project", min_free_bytes=0, min_free_inodes=0)], model_files=[])
    assert call_project("preflight_circuit_project", requirements, client, tmp_path, _TOOLS_BY_NAME)["checks_passed"]
    client.requirements = requirements
    execution = dict(operation="create_circuit_from_plan", arguments=dict(prepare_ref=prepared["prepare_ref"],
        request_id="inv-create", geometry_plan_digest=geometry["geometry_plan_digest"]))
    result = call_project("execute_circuit_operation", execution, client, tmp_path, _TOOLS_BY_NAME, bindings)
    receipt = result["receipt"]
    if design == "ldo_conflict":
        assert not receipt["ok"] and receipt["stage"] == "parameter_validation", result
        assert receipt["instance"] == "M5" and not receipt["view_created"] and not receipt["saved"], receipt
        assert "changed requested value" in receipt["error"], receipt
        assert skill(f'ddGetObj("cdfWork" "{design}" "schematic")') == "nil"
        assert skill(f'exists(cv dbGetOpenCellViews() cv~>libName=="cdfWork" && cv~>cellName=="{design}")') == "nil"
    else:
        assert receipt["ok"] and receipt["saved"] and receipt["readback_verified"], result
        assert receipt["check"] == [0, 0], receipt
    assert call_project("execute_circuit_operation", execution, client, tmp_path, _TOOLS_BY_NAME, bindings)["receipt"] == receipt
    (tmp_path / "creation-acceptance.json").write_text(json.dumps(result, indent=2))


def test_smic_ldo_parameter_conflicts(native, tmp_path):
    if not os.environ.get("CAD_AI_SMIC28_PDK"):
        pytest.skip("explicit licensed SMIC28 PDK root required")
    skill, call, registry, context = native
    evidence = {}
    for key, cell, parameters, order, expected in [
        ("original_tail", "n09_ckt", dict(w="2u", l="200n", fw="1u", fingers="1", m="1"),
         ["w", "l", "fw", "fingers", "m"], False),
        ("tail", "n09_ckt", dict(l="200n", fingers="1", w="2u", m="1"),
         ["l", "fingers", "w", "m"], True),
        ("pass", "p09_ckt", dict(l="30n", fingers="15", w="30u", m="1"),
         ["l", "fingers", "w", "m"], True),
        ("original_resistor", "rhrpo_2t_ckt", dict(calculatedParam="Resistance", r="40k"),
         ["calculatedParam", "r"], False),
        ("resistor", "rhrpo_2t_ckt", dict(calculatedParam="Length", r="40k"),
         ["calculatedParam", "r"], True),
    ]:
        capture = call("inspect_cdf", dict(target=dict(library="smic28hkmg", cell=cell, view="symbol")))
        result = registry.execute(ToolCall(key, "probe_cdf_parameters", dict(
            cdf_ref=capture["cdf_ref"], task_ref="task:cdf", request_id=key,
            target=dict(library="SicoTest", cell=key, view="schematic"),
            parameters=parameters, callback_order=order)), context)
        evidence[key] = result.data
        assert result.data["ok"] is expected, result.record()
        if expected:
            assert result.data["saved"] and result.data["readback_verified"], result.data
        else:
            assert not result.data["saved"] and "changed requested value" in result.data["error"], result.data
    (tmp_path / "ldo-parameter-acceptance.json").write_text(json.dumps(evidence, indent=2))
