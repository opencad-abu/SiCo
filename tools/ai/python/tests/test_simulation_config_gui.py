"""Generic Config handoff, effective binding and real Spectre behavior."""

import copy
import json
import os
import re
from pathlib import Path

import pytest
from circuit_create_support import AI_ROOT
from template_smic28_support import migration_session
from test_circuit_rc_gui import mcp_call
from test_simulation_recipe_failures_gui import terminal
from test_simulation_recipe_gui import load_backend


@pytest.mark.skipif(
    os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1", reason="licensed Config acceptance"
)
def test_config_binding_changes_netlist_and_result(tmp_path):
    verify_config_flow(tmp_path, "foreground")


@pytest.mark.skipif(
    os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1", reason="licensed Config acceptance"
)
def test_config_background_simulation(tmp_path):
    verify_config_flow(tmp_path, "background")


def verify_config_flow(tmp_path, presentation):
    with migration_session(tmp_path) as (b, s, proc):
        load_backend(b, tmp_path)
        for name in ["AI_circuit_config.il", "AI_simulation_config.il"]:
            b.skill("load(" + json.dumps(str(AI_ROOT / "skill" / name)) + ")")
        b.skill(
            'unless(ddGetObj("genericWork") ddCreateLib("genericWork" '
            + json.dumps(str(tmp_path / "library"))
            + "))"
        )
        b.skill("load(" + json.dumps(str(Path(__file__).with_name("config_flow_fixture.il"))) + ")")
        b.skill("configFixtureCreate()")

        def call(name, args, tag=None):
            result = mcp_call(s, name, args)
            if tag:
                (tmp_path / (tag + ".json")).write_text(json.dumps(result, indent=2))
            return result

        task = call(
            "begin_circuit_task",
            dict(task_id="config", presentation=presentation, choice_confirmed=True),
        )
        base = dict(
            library="genericWork",
            cell="config_tb",
            top_library="genericWork",
            top_cell="config_tb",
            task_ref=task["task_ref"],
            switch_views=["spectre", "schematic"],
            stop_views=["spectre"],
        )
        configs = {}
        for mode in ["same", "alt"]:
            args = {
                **base,
                "view": "cfg_" + mode,
                "request_id": mode,
                "bindings": [
                    dict(
                        instance="I_resblock_0",
                        library="genericWork",
                        cell="resblock",
                        view="schematic" if mode == "same" else "schematic_alt",
                    )
                ],
            }
            result = call("create_circuit_config", args, "config-" + mode)
            assert result["ok"] and result["saved"], result
            assert call("create_circuit_config", args) == result
            assert call("inspect_circuit_config", dict(config_ref=result["config_ref"]))["ok"]
            configs[mode] = result
        b.skill(
            'let((v c) v=dbOpenCellViewByType("genericWork" "resblock" "schematic_alt" "" "r") '
            'c=dbCopyCellView(v "genericWork" "otherblock" "schematic") '
            "dbSave(c) dbClose(c) dbClose(v))"
        )
        cross = call(
            "create_circuit_config",
            {
                **base,
                "view": "cross",
                "request_id": "cross",
                "bindings": [
                    dict(
                        instance="I_resblock_0",
                        library="genericWork",
                        cell="otherblock",
                        view="schematic",
                    )
                ],
            },
        )
        assert not cross["ok"] and cross["stage"] == "preflight", cross
        assert b.skill('ddGetObj("genericWork" "config_tb" "cross")') == "nil"
        tests = []
        for mode in ["direct", "same", "alt"]:
            test = dict(
                name=mode,
                design=dict(
                    library="genericWork",
                    cell="config_tb",
                    view="schematic" if mode == "direct" else "cfg_" + mode,
                ),
                simulator="spectre",
                variables={},
                models=[],
                analyses=[dict(name="dc", options=dict(saveOppoint=True))],
                outputs=[
                    dict(
                        name="voltage", kind="expression", value='VDC("/OUT")', plot=True, save=True
                    )
                ],
                switch_views=["spectre", "schematic"],
                stop_views=["spectre"],
            )
            if mode != "direct":
                test["config_ref"] = configs[mode]["config_ref"]
            tests.append(test)
        recipe = dict(
            schema="cad.simulation.recipe.v1",
            target=dict(library="genericWork", cell="config_comparison", view="maestro"),
            temperature_c=27,
            variables=dict(RLOAD="1K"),
            tests=tests,
            corners=[
                dict(name=n, temperature_c=t, variables={}, models=[])
                for n, t in [("room", 27), ("hot", 85)]
            ],
            sweeps=[dict(variable="RLOAD", values=[1000, 2000])],
        )
        preview = call("preview_simulation_recipe", dict(recipe=recipe), "preview")
        setup = call(
            "create_simulation_setup",
            dict(
                recipe=recipe,
                recipe_digest=preview["recipe_digest"],
                task_ref=task["task_ref"],
                request_id="comparison",
            ),
            "setup",
        )
        native_options = b.skill(
            'let((s) s=aiSimulationSetups["simulation:comparison"][\'session] '
            'aiCircuitJson(foreach(mapcar n maeGetSetup(?typeName "tests" ?session s) '
            "list(n maeGetEnvOption(n ?session s)))))"
        )
        (tmp_path / "native-options.json").write_text(native_options)
        assert setup["ok"] and setup["readback_verified"], setup
        b.skill(
            "aiWriteTextFile("
            + json.dumps(str(tmp_path / "sources-before.json"))
            + ' aiCircuitJson(aiSimulationSetups["simulation:comparison"][\'sources]))'
        )
        rejected = []
        for case in ["missing_ref", "mismatched_target", "mismatched_lists", "without_ref"]:
            bad = copy.deepcopy(recipe)
            bad["target"]["cell"] = case
            bad["tests"] = bad["tests"][1:2]
            if case == "missing_ref":
                bad["tests"][0]["config_ref"] = "config:absent"
            elif case == "mismatched_target":
                bad["tests"][0]["design"]["view"] = "cfg_alt"
            elif case == "mismatched_lists":
                bad["tests"][0]["switch_views"].reverse()
            else:
                bad["tests"][0].pop("config_ref")
            pre = call("preview_simulation_recipe", dict(recipe=bad))
            denied = call(
                "create_simulation_setup",
                dict(
                    recipe=bad,
                    recipe_digest=pre["recipe_digest"],
                    task_ref=task["task_ref"],
                    request_id=case,
                ),
            )
            assert not denied["ok"] and denied["stage"] == "preflight", denied
            assert b.skill('ddGetObj("genericWork" ' + json.dumps(case) + ' "maestro")') == "nil"
            rejected.append(denied)
        run = call(
            "run_simulation_setup",
            dict(setup_ref=setup["setup_ref"], request_id="comparison"),
            "run",
        )
        status = terminal(s, run)
        (tmp_path / "status.json").write_text(json.dumps(status, indent=2))
        assert status["state"] == "completed", status
        result = call(
            "read_maestro_results",
            dict(context_ref=setup["context_ref"], history=run["history"]),
            "results",
        )
        items = call(
            "query_maestro_results", dict(result_ref=result["result_ref"], limit=100), "values"
        )["items"]
        assert len(items) == 12, items
        for item in items:
            load = dict(
                next(
                    c[2]
                    for c in status["actual_coordinates"]
                    if c[0] == item["point"] and c[1] == item["corner"]
                )
            )["RLOAD"]
            expected = load / (load + (3000 if item["test"] == "alt" else 1000))
            assert item["value"] == pytest.approx(expected, abs=2e-8), item
        lists = {n: [] for n in ["direct", "same", "alt"]}
        for p in (tmp_path / "simulation").rglob("netlist/input.scs"):
            if "psf" in p.parts or any(part.startswith(".tmpADEDir") for part in p.parts):
                continue
            name = p.parts[-3]
            text = (p.parent / "netlist").read_text()
            text = re.sub(r"\\\s*\n", " ", text)
            lines = sorted(
                re.sub(r"\s+", " ", line.strip())
                for line in text.splitlines()
                if line.strip() and not line.lstrip().startswith("//")
            )
            lists[name].append(lines)
        assert len(lists["direct"]) == len(lists["same"]) == len(lists["alt"]) == 4, lists
        assert lists["direct"] == lists["same"], lists
        assert lists["same"] != lists["alt"], lists
        assert all(
            any("r=1K" in line or "r=1000" in line for line in rows) for rows in lists["same"]
        )
        assert all(
            any("r=3K" in line or "r=3000" in line for line in rows) for rows in lists["alt"]
        )
        assert call("inspect_simulation_setup", dict(setup_ref=setup["setup_ref"]))["ok"]
        b.skill(
            'let((h) h=hdbOpen("genericWork" "config_tb" "cfg_alt" "a") '
            'hdbSetDefaultViewListString(h "schematic spectre") hdbSave(h) hdbClose(h))'
        )
        changed = call(
            "run_simulation_setup",
            dict(setup_ref=setup["setup_ref"], request_id="config_changed"),
            "config-denied",
        )
        assert changed["state"] == "failed" and changed["history"] is None, changed
        b.skill(
            'let((h) h=hdbOpen("genericWork" "config_tb" "cfg_alt" "a") '
            'hdbSetDefaultViewListString(h "spectre schematic") hdbSave(h) hdbClose(h))'
        )
        assert call("inspect_simulation_setup", dict(setup_ref=setup["setup_ref"]))["ok"]
        # A saved change to a bound child must invalidate the existing simulation setup.
        b.skill(
            'let((cv) cv=dbOpenCellViewByType("genericWork" "resblock" "schematic_alt" "" "a") '
            'dbReplaceProp(dbFindAnyInstByName(cv "R1") "r" "string" "4K") schCheck(cv) '
            "dbSave(cv) dbClose(cv))"
        )
        denied = call(
            "run_simulation_setup",
            dict(setup_ref=setup["setup_ref"], request_id="child_changed"),
            "child-denied",
        )
        assert denied["state"] == "failed" and denied["history"] is None, denied
        b.skill(
            'let((cv) cv=dbOpenCellViewByType("genericWork" "resblock" "schematic_alt" "" "a") '
            'dbReplaceProp(dbFindAnyInstByName(cv "R1") "r" "string" "3K") schCheck(cv) '
            "dbSave(cv) dbClose(cv))"
        )
        b.skill(
            "aiWriteTextFile("
            + json.dumps(str(tmp_path / "sources-after.json"))
            + ' aiCircuitJson(aiSimSources(aiSimulationSetups["simulation:comparison"])))'
        )
        # Check & Save changes source revision metadata even after restoring a value.
        # Require a fresh setup record; never silently re-authorize the stale one.
        restored = call("inspect_simulation_setup", dict(setup_ref=setup["setup_ref"]))
        assert not restored["ok"], restored
        # Retain a fresh unchanged setup for foreground inspection after guard tests.
        fresh_recipe = copy.deepcopy(recipe)
        fresh_recipe["target"]["cell"] = "config_final"
        fresh_pre = call("preview_simulation_recipe", dict(recipe=fresh_recipe))
        fresh = call(
            "create_simulation_setup",
            dict(
                recipe=fresh_recipe,
                recipe_digest=fresh_pre["recipe_digest"],
                task_ref=task["task_ref"],
                request_id="final",
            ),
            "final-setup",
        )
        assert fresh["ok"] and fresh["readback_verified"], fresh
        final_run = call(
            "run_simulation_setup", dict(setup_ref=fresh["setup_ref"], request_id="final")
        )
        final_status = terminal(s, final_run)
        assert final_status["state"] == "completed", final_status
        assert call("inspect_simulation_setup", dict(setup_ref=fresh["setup_ref"]))["ok"]
        (tmp_path / "final-status.json").write_text(json.dumps(final_status, indent=2))
        (tmp_path / "acceptance.json").write_text(
            json.dumps(
                dict(
                    pid=proc.pid,
                    status=status,
                    final_setup=fresh,
                    final_status=final_status,
                    values=items,
                    netlists=lists,
                    child_guard=denied,
                    config_guard=changed,
                    preflight_rejections=rejected,
                ),
                indent=2,
            )
        )
