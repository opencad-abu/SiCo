"""Opt-in licensed selected-SMIC CDF extension acceptance, independent of PDK discovery."""

import json
import os
from pathlib import Path

import pytest
from circuit_create_support import AI_ROOT, session
from circuit_smic28_fixture import inverter
from test_circuit_rc_gui import mcp_call

from cadai.circuit_geometry import preview_geometry
from cadai.circuit_spec_plan import preview_circuit


def retarget(args, cell, extension=None):
    args["spec"]["target"]["cell"] = cell
    if extension:
        args["bindings"]["masters"][0]["callbacks"]["extension_ref"] = extension
    pre = preview_circuit(args["spec"], args["bindings"])
    args["preview_digest"] = pre["preview_digest"]
    args["geometry"]["binding_digest"] = pre["plan"]["binding_digest"]
    args.pop("geometry_plan_digest")
    args["geometry_plan_digest"] = preview_geometry(**args)["geometry_plan_digest"]
    return args


def test_smic_selected_extensions_and_restoration(tmp_path):
    if os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1" or not os.environ.get("CAD_AI_SMIC28_PDK"):
        pytest.skip("requires licensed GUI and explicit CAD_AI_SMIC28_PDK fixture root")
    pdk = Path(os.environ["CAD_AI_SMIC28_PDK"]).resolve()
    assert (pdk / "smic28hkmg/libInit.il").is_file()
    with session(tmp_path) as (bridge, server, _):
        with (tmp_path / "cds.lib").open("a") as f:
            f.write("DEFINE smic28hkmg " + str(pdk / "smic28hkmg") + "\n")
        project = tmp_path / "project"
        project.mkdir()
        bridge.skill(
            'progn(ddUpdateLibList() ddCreateLib("smicFlowTest" '
            + json.dumps(str(project / "library"))
            + "))"
        )
        task = mcp_call(
            server,
            "begin_circuit_task",
            dict(task_id="smic", presentation="background", choice_confirmed=True),
        )

        def prepare(args, req):
            return mcp_call(
                server,
                "prepare_circuit_creation",
                dict(**args, task_ref=task["task_ref"], request_id=req),
            )

        def execute(pre, args, req):
            return mcp_call(
                server,
                "create_circuit_from_plan",
                dict(
                    prepare_ref=pre["prepare_ref"],
                    geometry_plan_digest=args["geometry_plan_digest"],
                    request_id=req,
                ),
            )

        args = inverter(bridge, project)
        assert not prepare(args, "missing")["ok"]
        assert bridge.skill('ddGetObj("smicFlowTest" "smic_inv" "schematic")') == "nil"
        bridge.skill(
            "load(" + json.dumps(str(AI_ROOT / "python/tests/smic28_project_extension.il")) + ")"
        )
        bridge.skill('aiSmicTestInstall(ddGetObjReadPath(ddGetObj("smic28hkmg")))')
        before = bridge.skill(
            'aiCircuitJson(foreach(mapcar c list("n09_ckt" "p09_ckt") '
            'aiCrCdfRows(cdfGetCellCDF(ddGetObj("smic28hkmg" c)))))'
        )
        wrong = retarget(inverter(bridge, project), "wrong", "test:smic28:p09_ckt:v1")
        assert not prepare(wrong, "wrong")["ok"]
        changed = inverter(bridge, project, "function_changed")
        pre = prepare(changed, "function_changed")
        assert pre["ok"], pre
        bridge.skill(
            "progn(astOldCreator=getd('aiSmicTestCreate) "
            'procedure(aiSmicTestCreate(a b c d e f) error("must not run")))'
        )
        denied = execute(pre, changed, "function_changed")
        assert not denied["ok"] and not denied["view_created"], denied
        bridge.skill("putd('aiSmicTestCreate astOldCreator)")
        records = []
        for name, fingers, width in [("two", "2", "1u"), ("four", "4", "2u"), ("one", "1", "1u")]:
            data = inverter(bridge, project, name, fingers=fingers, width=width)
            pre = prepare(data, name)
            assert pre["ok"], pre
            created = execute(pre, data, name)
            assert created["ok"] and created["check"] == [0, 0] and created["readback_verified"], (
                created
            )
            assert execute(pre, data, name) == created
            records.append(created)
            assert mcp_call(
                server, "inspect_created_circuit", dict(circuit_ref=created["circuit_ref"])
            )["ok"]
        # Creator failure restores every effective-cell parameter; partial view stays open.
        failed_args = inverter(bridge, project, "partial")
        pre = prepare(failed_args, "partial")
        bridge.skill("aiSmicTestInjectFailure=t")
        failed = execute(pre, failed_args, "partial")
        bridge.skill("aiSmicTestInjectFailure=nil")
        assert not failed["ok"] and failed["view_created"] and not failed["saved"]
        assert (
            mcp_call(server, "release_created_circuit", dict(circuit_ref=failed["circuit_ref"]))[
                "code"
            ]
            == "partial_circuit_retained"
        )
        after = bridge.skill(
            'aiCircuitJson(foreach(mapcar c list("n09_ckt" "p09_ckt") '
            'aiCrCdfRows(cdfGetCellCDF(ddGetObj("smic28hkmg" c)))))'
        )
        assert before == after
        # Incorrect callback output cannot pass by merely becoming the effective baseline.
        bridge.skill(
            "progn(procedure(astWrong(a b c d e f) "
            "let((i) i=apply('aiSmicTestCreate list(a b c d e f)) "
            'dbReplaceProp(i "w" "string" "3u") i)) '
            'aiCrRegisterExtension("test:wrong:v1" "v1" '
            'list("smic28hkmg" "n09_ckt" "symbol") '
            "ddGetObjReadPath(ddGetObj(\"smic28hkmg\")) 'aiSmicTestValidate 'astWrong))"
        )
        bad = retarget(inverter(bridge, project), "wrong_width", "test:wrong:v1")
        pre = prepare(bad, "wrong_width")
        assert pre["ok"], pre
        failed_width = execute(pre, bad, "wrong_width")
        assert (
            not failed_width["ok"] and failed_width["view_created"] and not failed_width["saved"]
        ), failed_width
        (tmp_path / "smic-acceptance.json").write_text(
            json.dumps(
                dict(
                    records=records,
                    changed=denied,
                    failure=failed,
                    wrong_width=failed_width,
                    cdf_restored=True,
                ),
                indent=2,
            )
        )
