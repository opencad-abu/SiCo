"""Licensed generic Maestro recipe acceptance; old RC is only the source schematic fixture."""

import json
import os
import time
from copy import deepcopy

import pytest
from circuit_create_support import AI_ROOT, session
from simulation_recipe_fixture import recipe
from test_circuit_rc_gui import mcp_call


def load_backend(bridge, path):
    for module in (
        "AI_circuit_rc.il",
        "AI_maestro_results.il",
        "AI_simulation_check.il",
        "AI_simulation_config.il",
        "AI_simulation_dimensions.il",
        "AI_simulation_coordinates.il",
        "AI_simulation_setup.il",
        "AI_simulation_run.il",
    ):
        if not os.environ.get("CAD_AI_COMPILED_ROOT"):
            bridge.skill("load(" + json.dumps(str(AI_ROOT / "skill" / module)) + ")")
    if os.environ.get("CAD_AI_COMPILED_ROOT"):
        bridge.skill(
            'unless(and(aiSimulationRevision()=="20261021.ai.simulation.v8" '
            'getFnWriteProtect(\'aiSimSignalResolve) getFnWriteProtect(\'aiSimViewCoverage)) '
            'error("Compiled simulation repair missing"))'
        )
    bridge.skill(
        'envSetVal("asimenv.startup" "projectDir" \'string '
        + json.dumps(str(path / "simulation"))
        + ")"
    )


def source_fixture(bridge, server):
    task = mcp_call(
        server,
        "begin_circuit_task",
        dict(task_id="source", presentation="foreground", choice_confirmed=True),
    )
    preview = mcp_call(server, "preview_rc_circuit", dict(library="genericWork", cell="network"))
    created = mcp_call(
        server,
        "create_rc_circuit",
        dict(
            library="genericWork",
            cell="network",
            task_ref=task["task_ref"],
            request_id="source",
            preview_digest=preview["preview_digest"],
        ),
    )
    assert created["saved"], created
    bridge.skill(
        'let((cv) cv=dbOpenCellViewByType("genericWork" "network" "schematic" "" "a") '
        'dbReplaceProp(dbFindAnyInstByName(cv "R1") "r" "string" "RVAL") '
        'unless(cdfFindParamByName(cdfGetInstCDF(dbFindAnyInstByName(cv "V1")) "acm") '
        'error("fixture AC magnitude parameter absent")) '
        'dbReplaceProp(dbFindAnyInstByName(cv "V1") "acm" "string" "1") '
        'unless(equal(schCheck(cv) list(0 0)) error("fixture check failed")) '
        "dbSave(cv) dbClose(cv))"
    )


@pytest.mark.skipif(
    os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1", reason="licensed GUI/Spectre acceptance"
)
def test_nominal_multi_test_recipe_lifecycle(tmp_path):
    with session(tmp_path) as (bridge, server, process):
        load_backend(bridge, tmp_path)
        source_fixture(bridge, server)
        model = tmp_path / "explicit.scs"
        model.write_text(
            "simulator lang=spectre\nlibrary fixture\nsection tt\n"
            "parameters recipeProof=1\nendsection tt\nendlibrary fixture\n"
        )
        records = []
        for mode in ("background", "foreground"):
            task = mcp_call(
                server,
                "begin_circuit_task",
                dict(task_id=mode, presentation=mode, choice_confirmed=True),
            )
            spec = recipe(cell=mode, model=model)
            ac_test = deepcopy(spec["tests"][1])
            ac_test["name"] = "small_signal"
            ac_test["analyses"] = [
                {"name": "ac", "options": {"start": "1", "stop": "1M", "dec": "10"}}
            ]
            ac_test["outputs"][0]["value"] = 'value(mag(VF("/OUT")) 1e3)'
            spec["tests"].append(ac_test)
            preview = mcp_call(server, "preview_simulation_recipe", dict(recipe=spec))
            args = dict(
                recipe=spec,
                recipe_digest=preview["recipe_digest"],
                task_ref=task["task_ref"],
                request_id=mode,
            )
            created = mcp_call(server, "create_simulation_setup", args)
            assert created["ok"] and created["saved"] and created["readback_verified"], created
            assert created["window_open"] == (mode == "foreground")
            assert mcp_call(server, "create_simulation_setup", args) == created
            assert mcp_call(
                server, "inspect_simulation_setup", dict(setup_ref=created["setup_ref"])
            )["ok"]
            run = mcp_call(
                server,
                "run_simulation_setup",
                dict(setup_ref=created["setup_ref"], request_id=mode),
            )
            assert run["state"] == "running", run
            replay = mcp_call(
                server,
                "run_simulation_setup",
                dict(setup_ref=created["setup_ref"], request_id=mode),
            )
            assert replay["history"] == run["history"]
            end = time.monotonic() + 160
            while time.monotonic() < end:
                status = mcp_call(
                    server, "get_simulation_recipe_status", dict(run_ref=run["run_ref"])
                )
                if status["state"] != "running":
                    break
                time.sleep(0.5)
            (tmp_path / (mode + "-status.json")).write_text(json.dumps(status, indent=2))
            assert status["state"] == "completed", status
            result = mcp_call(
                server,
                "read_maestro_results",
                dict(context_ref=created["context_ref"], history=run["history"]),
            )
            assert result["ok"] and result["test_points"] == 3, result
            items = mcp_call(
                server, "query_maestro_results", dict(result_ref=result["result_ref"], limit=100)
            )["items"]
            assert {r["test"] for r in items} == {"bias", "settling", "small_signal"}
            assert next(r["value"] for r in items if r["test"] == "bias") == pytest.approx(
                1, abs=0.002
            )
            contract = dict(
                source="Explicit fixture acceptance",
                expected_test_points=preview["expected_test_points"],
                specifications=[
                    dict(test="bias", output="voltage", unit="V", lower=0.998, upper=1.002),
                    dict(test="settling", output="voltage", unit="V", lower=0.93, upper=0.95),
                    dict(test="small_signal", output="voltage", unit="V", lower=0.99, upper=1.0),
                ],
            )
            report = mcp_call(
                server,
                "evaluate_measurement_specs",
                dict(result_ref=result["result_ref"], contract=contract),
            )
            assert report["qualification"] == "pass", report
            released = mcp_call(
                server, "release_simulation_setup", dict(setup_ref=created["setup_ref"])
            )
            assert released["released"] and released["window_open"] == (mode == "foreground"), (
                released
            )
            records.append(dict(created=created, run=status, result=result, report=report))
        (tmp_path / "acceptance.json").write_text(json.dumps(records, indent=2))


@pytest.mark.skipif(
    os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1", reason="licensed GUI/Spectre acceptance"
)
def test_incomplete_view_list_fails_at_preflight_then_defaults_pass(tmp_path):
    """内置器件没有 schematic 视图：视图表在预检拒绝，缺省列表直接通过。"""
    with session(tmp_path) as (bridge, server, process):
        load_backend(bridge, tmp_path)
        source_fixture(bridge, server)
        task = mcp_call(
            server,
            "begin_circuit_task",
            dict(task_id="views", presentation="background", choice_confirmed=True),
        )
        bad = recipe(cell="views")
        for test in bad["tests"]:
            test["switch_views"] = ["schematic"]
            test["stop_views"] = ["schematic"]
        preview = mcp_call(server, "preview_simulation_recipe", dict(recipe=bad))
        refused = mcp_call(
            server,
            "create_simulation_setup",
            dict(recipe=bad, recipe_digest=preview["recipe_digest"],
                 task_ref=task["task_ref"], request_id="badviews"),
        )
        text = json.dumps(refused, ensure_ascii=False)
        (tmp_path / "refused.json").write_text(json.dumps(refused, ensure_ascii=False, indent=2))
        assert "view_list_incomplete" in text and "analogLib/vpulse" in text, refused
        assert refused["ok"] is False and refused["saved"] in (False, None), refused
        bridge.skill(
            'unless(and(!ddGetObj("genericWork" "views" "maestro") '
            '!ddGetObj("genericWork" "views")) error("refused creation left a target"))'
        )
        good = recipe(cell="views")
        for test in good["tests"]:
            del test["switch_views"]
            del test["stop_views"]
        preview = mcp_call(server, "preview_simulation_recipe", dict(recipe=good))
        created = mcp_call(
            server,
            "create_simulation_setup",
            dict(recipe=good, recipe_digest=preview["recipe_digest"],
                 task_ref=task["task_ref"], request_id="defaultviews"),
        )
        assert created["ok"] and created["saved"] and created["readback_verified"], created


@pytest.mark.skipif(
    os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1", reason="licensed GUI/Spectre acceptance"
)
def test_simulator_options_scale_reaches_the_spectre_netlist(tmp_path):
    with session(tmp_path) as (bridge, server, process):
        load_backend(bridge, tmp_path)
        source_fixture(bridge, server)
        task = mcp_call(
            server,
            "begin_circuit_task",
            dict(task_id="scale", presentation="background", choice_confirmed=True),
        )
        data = recipe(cell="scale_probe")
        for test in data["tests"]:
            test["simulator_options"] = {"scale": 0.9}
        preview = mcp_call(server, "preview_simulation_recipe", dict(recipe=data))
        assert preview["warnings"] == []
        created = mcp_call(
            server,
            "create_simulation_setup",
            dict(recipe=data, recipe_digest=preview["recipe_digest"],
                 task_ref=task["task_ref"], request_id="scale"),
        )
        assert created["ok"] and created["saved"] and created["readback_verified"], created
        assert created["window_open"] is False
        run = mcp_call(
            server, "run_simulation_setup", dict(setup_ref=created["setup_ref"], request_id="scale")
        )
        assert run["state"] == "running", run
        end = time.monotonic() + 160
        while time.monotonic() < end:
            status = mcp_call(server, "get_simulation_recipe_status", dict(run_ref=run["run_ref"]))
            if status["state"] != "running":
                break
            time.sleep(0.5)
        (tmp_path / "scale-status.json").write_text(json.dumps(status, indent=2))
        assert status["state"] == "completed", status
        hits, seen = [], []
        for path in sorted((tmp_path / "simulation").rglob("*")):
            if not path.is_file():
                continue
            try:
                text = path.read_text(errors="ignore")
            except OSError:
                continue
            if "scale" in text:
                seen.append(str(path.relative_to(tmp_path)))
            if "scale=0.9" in text.replace(" ", ""):
                hits.append(str(path.relative_to(tmp_path)))
        (tmp_path / "scale-evidence.json").write_text(
            json.dumps({"scale_mentions": seen, "scale_value": hits}, indent=2)
        )
        assert hits, "scale=0.9 absent from the generated spectre netlist"
        released = mcp_call(
            server, "release_simulation_setup", dict(setup_ref=created["setup_ref"])
        )
        assert released["released"]


def create_setup(server, name, model=None):
    data = recipe(cell=name, model=model)
    pre = mcp_call(server, "preview_simulation_recipe", {"recipe": data})
    args = dict(
        recipe=data, recipe_digest=pre["recipe_digest"], task_ref="task:source", request_id=name
    )
    created = mcp_call(server, "create_simulation_setup", args)
    assert created["ok"], created
    return created, args


@pytest.mark.skipif(
    os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1", reason="licensed native lifecycle guards"
)
def test_recipe_refuses_modified_models_sources_and_setup(tmp_path):
    with session(tmp_path) as (bridge, server, process):
        load_backend(bridge, tmp_path)
        source_fixture(bridge, server)
        model = tmp_path / "explicit.scs"
        model.write_text(
            "simulator lang=spectre\nlibrary fixture\nsection tt\n"
            "endsection tt\nendlibrary fixture\n"
        )
        setup, args = create_setup(server, "guarded", model)
        old = model.read_text()
        model.write_text(old + "// model changed\n")
        changed = mcp_call(server, "inspect_simulation_setup", {"setup_ref": setup["setup_ref"]})
        assert changed["code"] == "simulation_setup_changed"
        failed = mcp_call(
            server,
            "run_simulation_setup",
            dict(setup_ref=setup["setup_ref"], request_id="model_changed"),
        )
        assert failed["state"] == "failed" and failed["history"] is None
        model.write_text(old)
        assert (
            mcp_call(
                server,
                "run_simulation_setup",
                dict(setup_ref=setup["setup_ref"], request_id="model_changed"),
            )
            == failed
        )
        assert mcp_call(server, "inspect_simulation_setup", {"setup_ref": setup["setup_ref"]})["ok"]
        # Existing target with a new request is refused before opening a write session.
        collision = mcp_call(server, "create_simulation_setup", {**args, "request_id": "collision"})
        assert not collision["ok"] and collision["stage"] == "preflight"
        # User changes are neither saved nor normalized by inspect/run/release.
        bridge.skill(
            'maeSetVar("RVAL" "9K" ?session aiSimulationSetups["simulation:guarded"][\'session])'
        )
        assert (
            mcp_call(server, "inspect_simulation_setup", {"setup_ref": setup["setup_ref"]})["code"]
            == "simulation_setup_changed"
        )
        assert (
            mcp_call(server, "release_simulation_setup", {"setup_ref": setup["setup_ref"]})["code"]
            == "release_failed"
        )
        assert mcp_call(server, "create_simulation_setup", args) == setup
        # Separate record for source modification; previous user-edited setup is retained.
        source_setup, _ = create_setup(server, "source_guard")
        bridge.skill(
            'let((cv) cv=dbOpenCellViewByType("genericWork" "network" "schematic" "" "a") '
            'dbReplaceProp(dbFindAnyInstByName(cv "R1") "r" "string" "22K") dbClose(cv))'
        )
        result = mcp_call(
            server,
            "run_simulation_setup",
            dict(setup_ref=source_setup["setup_ref"], request_id="source_changed"),
        )
        assert result["state"] == "failed" and result["history"] is None
        assert (
            bridge.skill(
                'maeIsSetupModified(?session aiSimulationSetups["simulation:guarded"][\'session])'
            )
            == "t"
        )
        (tmp_path / "guard-results.json").write_text(
            json.dumps(dict(model=failed, collision=collision, source=result), indent=2)
        )
