"""Licensed CDF tool acceptance in owned sessions, independent of the PDK producer."""

import json
import os
from pathlib import Path

import pytest
from circuit_create_support import AI_ROOT, session
from test_circuit_rc_gui import mcp_call


def load(bridge):
    for name in ("AI_pdk_init.il", "AI_cdf_data.il", "AI_cdf_transfer.il", "AI_test_workspace.il", "AI_cdf_probe.il"):
        bridge.skill("load(" + json.dumps(str(AI_ROOT / "skill" / name)) + ")")


def inspect(server, library="analogLib", cell="res", **options):
    return mcp_call(
        server,
        "inspect_cdf",
        dict(target=dict(library=library, cell=cell, view="symbol"), **options),
    )


def request(cdf, cell, task="task:cdf", **extra):
    return dict(
        cdf_ref=cdf["cdf_ref"],
        task_ref=task,
        request_id=cell,
        target=dict(library="SicoTest", cell=cell, view="schematic"),
        parameters={"r": "2K"},
        **extra,
    )


@pytest.mark.skipif(os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1", reason="licensed GUI")
def test_inspect_and_probe_static(tmp_path):
    with session(tmp_path) as (bridge, server, _):
        load(bridge)
        bridge.skill(
            "progn(cdfOutputCheckpoint=getd('aiCdfProbeCheckpoint) "
            "cdfOutputSnapshot=getd('aiCdfSnapshot) "
            'procedure(cdfOutputRead(@rest cdfOutputReadArgs) '
            'printf("CDF_INSPECT_READ\\n") warn("CDF_INSPECT_WARNING\\n") '
            'apply(cdfOutputSnapshot cdfOutputReadArgs)) '
            "putd('aiCdfSnapshot getd('cdfOutputRead)) "
            'procedure(cdfOutputStep(cdfOutputData cdfOutputLabel) '
            'printf("CDF_STEP:%s\\n" cdfOutputLabel) '
            'apply(cdfOutputCheckpoint list(cdfOutputData cdfOutputLabel))) '
            "putd('aiCdfProbeCheckpoint getd('cdfOutputStep)) "
            'cdfOriginalPo=poport cdfOriginalWo=woport cdfOriginalErr=errport t)'
        )
        absent = inspect(server, "basic", "gnd")
        # Cadence may return an effective library-level CDF container with zero params.
        assert absent["ok"] and absent["parameters"] == []
        unavailable = mcp_call(server, "inspect_cdf", dict(cdf_ref="cdf:" + "0" * 32))
        assert not unavailable["ok"]
        for mode in ("foreground", "background"):
            task = mcp_call(
                server,
                "begin_circuit_task",
                dict(task_id=mode, presentation=mode, choice_confirmed=True),
            )
            captured = inspect(server, limit=2)
            assert captured["ok"] and not captured["callbacks_executed"], captured
            rejected = json.loads(bridge.skill(
                'aiCdfProbe(' + json.dumps(captured["cdf_ref"]) + ' '
                + json.dumps(task["task_ref"]) + ' "outside-' + mode + '" '
                'list("genericWork" "must_not_create" "schematic") nil nil '
                '"cdf-probe:outside-' + mode + '")'))
            assert not rejected["ok"] and not rejected["view_created"]
            assert "SicoTest" in rejected["error"]
            assert bridge.skill('ddGetObj("genericWork" "must_not_create")') == "nil"
            assert captured["output_capture"] == "skill_ports"
            assert "CDF_INSPECT_READ\n" in captured["output"]
            assert "CDF_INSPECT_WARNING" in captured["output"]
            assert captured["next_offset"] == 2
            page = mcp_call(
                server,
                "inspect_cdf",
                dict(cdf_ref=captured["cdf_ref"], parameters=["r", "nonexistent"]),
            )
            assert len(page["parameters"]) == 1 and page["missing_parameters"] == ["nonexistent"]
            assert page["output"] == ""
            assert page["parameters"][0]["current"]["status"] == "not_applicable"
            args = request(captured, mode, task["task_ref"])
            windows = bridge.skill("hiGetWindowList('all)")
            result = mcp_call(server, "probe_cdf_parameters", args)
            assert result["ok"] and result["saved"] and result["readback_verified"], result
            assert result["cdf_values_restored"] and result["all_requested_match"]
            assert result["presentation"] == "background" and not result["window_open"]
            assert bridge.skill("hiGetWindowList('all)") == windows
            assert bridge.skill('aiCircuitTasks["' + task["task_ref"] + '"]') == mode
            assert bridge.skill('simplifyFilename(ddGetObjReadPath(ddGetObj("SicoTest")))') == str(tmp_path / "SicoTest")
            assert result["trace_coverage"] == "boundary_only"
            assert result["output_capture"] == "skill_ports" and not result["output_truncated"]
            assert "CDF_STEP:cell_before\n" in result["output"]
            assert "CDF_STEP:instance_final\n" in result["output"]
            assert bridge.skill('and(poport==cdfOriginalPo woport==cdfOriginalWo '
                                'errport==cdfOriginalErr)') == "t"
            repeat = mcp_call(server, "probe_cdf_parameters", args)
            assert repeat == result
            assert mcp_call(server, "get_cdf_probe", dict(probe_ref=result["probe_ref"])) == result
            inst = mcp_call(
                server,
                "inspect_cdf",
                dict(target=args["target"], instance="I_CDF_PROBE_0", parameters=["r"]),
            )
            row = inst["parameters"][0]
            assert (
                row["current"]["value"] == "2K" and row["instance_property"]["status"] == "present"
            )
            assert row["instance_property"]["value"]["value"] == "2K"
            assert not result["electrical_checked"]
            assert Path(result["artifact"]["path"]).is_file()
            occupied = mcp_call(
                server, "probe_cdf_parameters", dict(args, request_id=mode + "exists")
            )
            assert not occupied["ok"] and not occupied["view_created"]
            assert "probe target exists" in occupied["output"]
        # Fresh unsaved instance inspection, no user check/save or close.
        bridge.skill(
            'heldCdfView=dbOpenCellViewByType("SicoTest" "foreground" "schematic" "" "a")'
        )
        bridge.skill(
            'dbReplaceProp(dbFindAnyInstByName(heldCdfView "I_CDF_PROBE_0") "r" "string" "3K")'
        )
        fresh = mcp_call(
            server,
            "inspect_cdf",
            dict(
                target=dict(library="SicoTest", cell="foreground", view="schematic"),
                instance="I_CDF_PROBE_0",
                parameters=["r"],
            ),
        )
        assert fresh["target_modified"] and fresh["parameters"][0]["current"]["value"] == "3K"
        frozen = mcp_call(server, "inspect_cdf", dict(cdf_ref=inst["cdf_ref"], parameters=["r"]))
        assert frozen["parameters"][0]["current"]["value"] == "2K"
        assert bridge.skill("dbIsCellViewModified(heldCdfView)") == "t"
        # Changed defaults invalidate a capture, and restore test-owned in-memory CDF.
        stale = inspect(server)
        bridge.skill(
            'progn(cdfTestP=cdfFindParamByName(cdfGetBaseCellCDF(ddGetObj("analogLib" "res")) "r") '
            'cdfTestDefault=cdfTestP~>defValue cdfTestP~>defValue="77K")'
        )
        denied = mcp_call(
            server, "probe_cdf_parameters", request(stale, "stale", "task:background")
        )
        assert not denied["ok"] and not denied["view_created"]
        bridge.skill("cdfTestP~>defValue=cdfTestDefault")
        (tmp_path / "static.json").write_text(
            json.dumps(dict(result=result, stale=denied, fresh=fresh), indent=2)
        )


@pytest.mark.skipif(
    os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1" or not os.environ.get("SICO_AI_SMIC28_PDK"),
    reason="licensed SMIC28",
)
def test_probe_smic_trace_restore_failure(tmp_path):
    with session(tmp_path) as (bridge, server, _):
        pdk = Path(os.environ["SICO_AI_SMIC28_PDK"])
        with (tmp_path / "cds.lib").open("a") as f:
            f.write("DEFINE smic28hkmg " + str(pdk / "smic28hkmg") + "\n")
        bridge.skill("ddUpdateLibList()")
        load(bridge)
        captured = inspect(server, "smic28hkmg", "n09_ckt", parameters=["w", "l", "fingers", "m"])
        assert captured["ok"], captured
        assert (
            next(p for p in captured["parameters"] if p["name"] == "w")["editable"]["status"]
            == "context_required"
        )
        bridge.skill(
            "load(" + json.dumps(str(AI_ROOT / "python/tests/smic28_project_extension.il")) + ")"
        )
        bridge.skill('aiSmicTestInstall(ddGetObjReadPath(ddGetObj("smic28hkmg")))')
        mcp_call(
            server,
            "begin_circuit_task",
            dict(task_id="cdf", presentation="background", choice_confirmed=True),
        )
        args = request(captured, "smic", extension_ref="test:smic28:n09_ckt:v1")
        args["parameters"] = dict(l="60n", w="1u", fingers="2", m="1")
        result = mcp_call(server, "probe_cdf_parameters", args)
        assert result["ok"] and result["all_requested_match"] and result["cdf_values_restored"], (
            result
        )
        assert result["trace_coverage"] == "project_checkpoints"
        steps = {s["label"]: s for s in result["trace"]}
        fingers = {c["name"]: c for c in steps["project:set:fingers"]["changes"]}
        assert fingers["w"]["after"]["value"] == "2u"
        width = {c["name"]: c for c in steps["project:set:w"]["changes"]}
        assert width["w"]["after"]["value"] == "1u"
        assert mcp_call(server, "get_cdf_probe", dict(probe_ref=result["probe_ref"])) == result
        missing = dict(
            args,
            request_id="missing",
            target=dict(library="SicoTest", cell="missing", view="schematic"),
        )
        missing.pop("extension_ref")
        assert not mcp_call(server, "probe_cdf_parameters", missing)["view_created"]
        bridge.skill("aiSmicTestInjectFailure=t")
        failed_args = dict(
            args,
            request_id="failed",
            target=dict(library="SicoTest", cell="failed", view="schematic"),
        )
        failed = mcp_call(server, "probe_cdf_parameters", failed_args)
        bridge.skill("aiSmicTestInjectFailure=nil")
        assert not failed["ok"] and failed["view_created"] and failed["cdf_values_restored"], failed
        assert not failed["saved"]
        assert mcp_call(server, "probe_cdf_parameters", failed_args) == failed
        # Wrong values are diagnostic results, not falsely successful parameter matches.
        bridge.skill(
            "progn(procedure(cdfWrong(cv master name xy orient rows) "
            "let((i) i=aiSmicTestCreate(cv master name xy orient rows) "
            'dbReplaceProp(i "w" "string" "3u") i)) '
            'aiCrRegisterExtension("test:cdfwrong" "1" '
            'list("smic28hkmg" "n09_ckt" "symbol") ddGetObjReadPath(ddGetObj("smic28hkmg")) '
            "'aiSmicTestValidate 'cdfWrong))"
        )
        wrong_args = dict(
            args,
            request_id="wrong",
            extension_ref="test:cdfwrong",
            target=dict(library="SicoTest", cell="wrong", view="schematic"),
        )
        wrong = mcp_call(server, "probe_cdf_parameters", wrong_args)
        assert wrong["ok"] and not wrong["all_requested_match"], wrong
        # Replacing registered code cannot silently change a future probe's behavior.
        bridge.skill(
            'progn(cdfOld=getd(\'cdfWrong) procedure(cdfWrong(a b c d e f) error("must not run")))'
        )
        rebound = mcp_call(
            server,
            "probe_cdf_parameters",
            dict(
                wrong_args,
                request_id="rebound",
                target=dict(library="SicoTest", cell="rebound", view="schematic"),
            ),
        )
        assert not rebound["ok"] and not rebound["view_created"]
        bridge.skill("putd('cdfWrong cdfOld)")
        # An extension that does not restore a changed cell value is still contained by
        # the probe's scalar restoration; this is not a general callback side-effect rollback.
        bridge.skill(
            "progn(procedure(cdfThrow(cv master name xy orient rows) "
            'cdfFindParamByName(cdfGetCellCDF(ddGetObj("smic28hkmg" "n09_ckt")) "w")~>value="9u" '
            'error("test callback failure")) '
            'aiCrRegisterExtension("test:cdfthrow" "1" list("smic28hkmg" "n09_ckt" "symbol") '
            "ddGetObjReadPath(ddGetObj(\"smic28hkmg\")) 'aiSmicTestValidate 'cdfThrow))"
        )
        thrown = mcp_call(
            server,
            "probe_cdf_parameters",
            dict(
                args,
                extension_ref="test:cdfthrow",
                request_id="thrown",
                target=dict(library="SicoTest", cell="thrown", view="schematic"),
            ),
        )
        assert (
            not thrown["ok"] and thrown["cdf_values_restored"] and thrown["cdf_metadata_unchanged"]
        )
        (tmp_path / "smic.json").write_text(
            json.dumps(
                dict(result=result, failed=failed, wrong=wrong, rebound=rebound, thrown=thrown),
                indent=2,
            )
        )
