"""Licensed output-type readback and run acceptance in an owned Virtuoso process."""

import json
import os
import time

import pytest
from circuit_create_support import AI_ROOT, session
from simulation_recipe_fixture import recipe
from test_circuit_rc_gui import mcp_call
from test_simulation_recipe_gui import load_backend, source_fixture


@pytest.mark.skipif(os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1", reason="licensed GUI acceptance")
def test_terminal_outputs_survive_save_reopen_and_simulation(tmp_path):
    with session(tmp_path) as (bridge, server, _):
        load_backend(bridge, tmp_path)
        source_fixture(bridge, server)
        for signal_type in (None, "terminal"):
            name = "wrong_net" if signal_type is None else "terminal_current"
            spec = recipe(cell=name)
            spec["tests"] = spec["tests"][:1]
            spec["tests"][0]["outputs"].append(
                dict(
                    name="IDD",
                    kind="signal",
                    value="/V1/PLUS",
                    plot=True,
                    save=True,
                    **({"signal_type": signal_type} if signal_type else {}),
                )
            )
            if signal_type:
                spec["tests"][0]["outputs"].append(
                    dict(
                        name="VTERM",
                        kind="signal",
                        value="/V1/PLUS",
                        signal_type="terminalV",
                        plot=True,
                        save=True,
                    )
                )
            preview = mcp_call(server, "preview_simulation_recipe", dict(recipe=spec))
            created = mcp_call(
                server,
                "create_simulation_setup",
                dict(
                    recipe=spec,
                    recipe_digest=preview["recipe_digest"],
                    task_ref="task:source",
                    request_id=name,
                ),
            )
            if signal_type is None:
                assert not created["ok"] and not created["saved"], created
                error = created.get("error", created.get("message", ""))
                assert all(word in error for word in ("IDD", "/V1/PLUS", "stop view")), created
                assert created["stage"] == "preflight", created
                assert bridge.skill('ddGetObj("genericWork" "wrong_net" "maestro")') == "nil"
                assert bridge.skill("maeGetSessions()") == "nil"
                continue
            assert created["ok"] and created["saved"] and created["readback_verified"], created
            inspected = mcp_call(
                server, "inspect_simulation_setup", dict(setup_ref=created["setup_ref"])
            )
            assert inspected["ok"], inspected
            run = mcp_call(
                server,
                "run_simulation_setup",
                dict(setup_ref=created["setup_ref"], request_id=name),
            )
            assert run["state"] == "running", run
            deadline = time.monotonic() + 160
            while time.monotonic() < deadline:
                status = mcp_call(
                    server, "get_simulation_recipe_status", dict(run_ref=run["run_ref"])
                )
                if status["state"] != "running":
                    break
                time.sleep(0.5)
            assert status["state"] == "completed", status
            (tmp_path / "terminal-acceptance.json").write_text(
                json.dumps(dict(created=created, inspected=inspected, run=status), indent=2)
            )
            # The signal kind is part of the retained setup identity, even when
            # signal path, plot and save remain unchanged.
            bridge.skill(
                'let((o) o=car(setof(x maeGetTestOutputs("settling" '
                '?session aiSimulationSetups["simulation:terminal_current"][\'session]) '
                'x->name=="IDD")) o->type=\'terminalV t)'
            )
            changed = mcp_call(
                server, "inspect_simulation_setup", dict(setup_ref=created["setup_ref"])
            )
            assert not changed["ok"], changed


@pytest.mark.skipif(os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1", reason="licensed GUI acceptance")
def test_signal_paths_resolve_effective_hierarchy_before_setup_creation(tmp_path):
    from copy import deepcopy
    from pathlib import Path

    with session(tmp_path) as (bridge, server, _):
        load_backend(bridge, tmp_path)
        if not os.environ.get("CAD_AI_COMPILED_ROOT"):
            bridge.skill(f"load({json.dumps(str(AI_ROOT / 'skill/AI_circuit_config.il'))})")
        bridge.skill(f"load({json.dumps(str(Path(__file__).with_name('config_flow_fixture.il')))})")
        bridge.skill("configFixtureCreate()")
        task = mcp_call(
            server,
            "begin_circuit_task",
            dict(task_id="signals", presentation="background", choice_confirmed=True),
        )
        # An instance present only in the alternate view proves we use the actual
        # Config binding rather than the symbol or a guessed schematic view.
        bridge.skill(
            'let((v) v=dbOpenCellViewByType("genericWork" "resblock" "schematic_alt" "" "a") '
            'dbFindAnyInstByName(v "R1")~>name="RALT" '
            'unless(equal(schCheck(v) list(0 0)) error("alternate fixture check failed")) '
            "dbSave(v) dbClose(v))"
        )
        config = mcp_call(
            server,
            "create_circuit_config",
            dict(
                library="genericWork",
                cell="config_tb",
                view="cfg_signals",
                top_library="genericWork",
                top_cell="config_tb",
                task_ref=task["task_ref"],
                request_id="signal_config",
                switch_views=["spectre", "schematic"],
                stop_views=["spectre"],
                bindings=[
                    dict(
                        instance="I_resblock_0",
                        library="genericWork",
                        cell="resblock",
                        view="schematic_alt",
                    )
                ],
            ),
        )
        assert config["ok"], config
        base = recipe(cell="hierarchy_signals")
        base["variables"] = {"RLOAD": "1K"}
        base["tests"] = base["tests"][:1]
        test = base["tests"][0]
        test.update(
            variables={},
            design=dict(library="genericWork", cell="config_tb", view="schematic"),
            outputs=[],
            analyses=[dict(name="dc", options={})],
        )
        records = []

        def create(spec, name):
            spec["target"]["cell"] = name
            preview = mcp_call(server, "preview_simulation_recipe", dict(recipe=spec))
            result = mcp_call(
                server,
                "create_simulation_setup",
                dict(
                    recipe=spec,
                    recipe_digest=preview["recipe_digest"],
                    task_ref=task["task_ref"],
                    request_id=name,
                ),
            )
            records.append(result)
            (tmp_path / "signal-path-acceptance.json").write_text(json.dumps(records, indent=2))
            return result

        def view_state():
            return bridge.skill(
                'sort(foreach(mapcar v setof(x dbGetOpenCellViews() '
                'x~>libName=="genericWork" && x~>cellViewType=="schematic") '
                'sprintf(nil "%L" list(v~>cellName v~>viewName dbIsCellViewModified(v)))) '
                "'alphalessp)"
            )

        # A failed traversal must preserve its cause across HDB/OA cleanup.
        bridge.skill(
            'let((v) v=dbOpenCellViewByType("genericWork" "resblock" "schematic_alt" "" "a") '
            'dbReplaceProp(dbFindAnyInstByName(v "RALT") "r" "string" "4K") '
            "dbSave(v) dbClose(v))"
        )
        unclean = deepcopy(base)
        unclean["tests"][0]["design"]["view"] = "cfg_signals"
        unclean["tests"][0]["config_ref"] = config["config_ref"]
        unclean["tests"][0]["outputs"] = [
            dict(name="voltage", kind="expression", value='VDC("/OUT")', plot=True, save=True)
        ]
        before_views = view_state()
        result = create(unclean, "unclean_child")
        assert not result["ok"] and result["stage"] == "preflight", result
        assert "saved clean data" in result["error"] and "schematic_alt" in result["error"], result
        assert bridge.skill('ddGetObj("genericWork" "unclean_child" "maestro")') == "nil"
        assert bridge.skill("maeGetSessions()") == "nil"
        assert view_state() == before_views
        bridge.skill(
            'let((v) v=dbOpenCellViewByType("genericWork" "resblock" "schematic_alt" "" "a") '
            'unless(equal(schCheck(v) list(0 0)) error("alternate fixture check failed")) '
            "dbSave(v) dbClose(v))"
        )

        cases = [
            ("net", "/MISSING", False, False),
            ("terminal", "/VSUP/MISSING", False, False),
            ("net", "/VSUP/PLUS", False, False),
            ("terminal", "/I_resblock_0/RALT/PLUS", False, False),
            ("net", "/I_resblock_0/RALT/PLUS", True, False),
            ("terminalV", "/I_resblock_0/MISSING/PLUS", True, False),
            ("terminal", "/I_resblock_0/RALT/PLUS", True, True),
            ("net", "/I_resblock_0/P", True, True),
            ("net", "/I_resblock_0/P", False, True),
            ("terminal", "/I_resblock_0/R1/PLUS", False, True),
            ("terminalV", "/I_resblock_0/RALT/PLUS", True, True),
        ]
        for index, (kind, path, configured, valid) in enumerate(cases):
            spec = deepcopy(base)
            spec["tests"][0]["outputs"] = [
                dict(
                    name="probe", kind="signal", value=path, signal_type=kind, plot=True, save=True
                )
            ]
            if configured:
                spec["tests"][0]["design"]["view"] = "cfg_signals"
                spec["tests"][0]["config_ref"] = config["config_ref"]
            name = "path_" + str(index)
            before = bridge.skill("maeGetSessions()")
            before_views = view_state()
            result = create(spec, name)
            if valid:
                assert result["ok"] and result["saved"] and result["readback_verified"], result
                assert mcp_call(
                    server, "release_simulation_setup", dict(setup_ref=result["setup_ref"])
                )["released"]
            else:
                assert not result["ok"] and result["stage"] == "preflight", result
                assert path in result["error"] and "probe" in result["error"], result
                assert bridge.skill(f'ddGetObj("genericWork" "{name}" "maestro")') == "nil"
                assert bridge.skill("maeGetSessions()") == before
                assert view_state() == before_views

        retained = []
        for configured in (False, True):
            spec = deepcopy(base)
            spec["tests"][0]["outputs"] = [
                dict(name="child_net", kind="signal", value="/I_resblock_0/P", plot=True, save=True)
            ]
            if configured:
                spec["tests"][0]["design"]["view"] = "cfg_signals"
                spec["tests"][0]["config_ref"] = config["config_ref"]
            result = create(spec, "retained_" + str(int(configured)))
            assert result["ok"], result
            retained.append(result)
        for index, (view, instance) in enumerate((("schematic", "R1"), ("schematic_alt", "RALT"))):
            bridge.skill(
                f'let((v) v=dbOpenCellViewByType("genericWork" "resblock" "{view}" "" "a") '
                f'dbReplaceProp(dbFindAnyInstByName(v "{instance}") "r" "string" "5K") '
                'unless(equal(schCheck(v) list(0 0)) error("child change check failed")) '
                "dbSave(v) dbClose(v))"
            )
            changed = mcp_call(
                server, "inspect_simulation_setup", dict(setup_ref=retained[index]["setup_ref"])
            )
            assert not changed["ok"] and "source schematics" in changed["message"], changed
            denied = mcp_call(
                server,
                "run_simulation_setup",
                dict(setup_ref=retained[index]["setup_ref"], request_id="changed_" + str(index)),
            )
            assert denied["state"] == "failed" and denied["history"] is None, denied
            records.append(dict(changed=changed, denied=denied))
            if index == 0:
                unaffected = mcp_call(
                    server, "inspect_simulation_setup", dict(setup_ref=retained[1]["setup_ref"])
                )
                assert unaffected["ok"], unaffected
        (tmp_path / "signal-path-acceptance.json").write_text(json.dumps(records, indent=2))
