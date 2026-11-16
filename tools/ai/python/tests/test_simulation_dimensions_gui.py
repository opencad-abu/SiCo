"""Native generic dimension readback, actual netlists and failure guards."""

import json
import os
import re

import pytest
from circuit_create_support import session
from simulation_dimensions_fixture import dimension_recipe
from test_circuit_rc_gui import mcp_call
from test_simulation_recipe_failures_gui import terminal
from test_simulation_recipe_gui import load_backend, source_fixture


@pytest.mark.skipif(
    os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1", reason="licensed matrix acceptance"
)
def test_generic_corners_and_two_axes(tmp_path):
    with session(tmp_path) as (bridge, server, _):
        load_backend(bridge, tmp_path)
        source_fixture(bridge, server)
        # Source fixture only: make both numerical axes affect this passive circuit.
        bridge.skill(
            'let((cv) cv=dbOpenCellViewByType("genericWork" "network" "schematic" "" "a") '
            'dbReplaceProp(dbFindAnyInstByName(cv "C1") "c" "string" "CVAL") '
            "schCheck(cv) dbSave(cv) dbClose(cv))"
        )
        model = tmp_path / "model.scs"
        model.write_text(
            "simulator lang=spectre\nlibrary fixture\n"
            + "".join(
                f"section {n}\nparameters modelProof={i}\nendsection {n}\n"
                for i, n in enumerate(("tt", "ff", "ss"))
            )
            + "endlibrary fixture\n"
        )
        data = dimension_recipe(model)
        preview = mcp_call(server, "preview_simulation_recipe", dict(recipe=data))
        args = dict(
            recipe=data,
            recipe_digest=preview["recipe_digest"],
            task_ref="task:source",
            request_id="matrix",
        )
        setup = mcp_call(server, "create_simulation_setup", args)
        (tmp_path / "setup.json").write_text(json.dumps(setup, indent=2))
        assert setup["ok"] and setup["readback_verified"], setup
        assert mcp_call(server, "create_simulation_setup", args) == setup
        run = mcp_call(
            server, "run_simulation_setup", dict(setup_ref=setup["setup_ref"], request_id="matrix")
        )
        assert run["state"] == "running", run
        status = terminal(server, run)
        (tmp_path / "status.json").write_text(json.dumps(status, indent=2))
        assert status["state"] == "completed" and status["progress"] == [24, 24], status
        assert len(status["actual_coordinates"]) == 12
        result = mcp_call(
            server,
            "read_maestro_results",
            dict(context_ref=setup["context_ref"], history=run["history"]),
        )
        assert result["ok"] and result["test_points"] == 24, result
        (tmp_path / "results.json").write_text(json.dumps(result, indent=2))
        netlists = list((tmp_path / "simulation").rglob("netlist/input.scs"))
        coords = set()
        for path in netlists:
            if "psf" in path.parts or any(part.startswith(".tmpADEDir") for part in path.parts):
                continue
            text = path.read_text()
            section = re.search(r'include "[^"\n]+model.scs" section=(tt|ff|ss)', text)
            assert section, text[:700]
            r = float(re.search(r"\bRVAL=([0-9.eE+-]+)", text).group(1))
            c = float(re.search(r"\bCVAL=([0-9.eE+-]+)", text).group(1))
            t = float(re.search(r"\btemp=([0-9.eE+-]+)", text).group(1))
            offset = float(re.search(r"\bOFFSET=([0-9.eE+-]+)", text).group(1))
            name = section.group(1)
            assert (t, offset) == {"tt": (27, 0), "ff": (-40, 1), "ss": (125, -1)}[name]
            coords.add((name, r, c, path.parts[-3]))
        assert len(coords) == 24, coords
        assert mcp_call(server, "inspect_simulation_setup", dict(setup_ref=setup["setup_ref"]))[
            "ok"
        ]
        sid = 'aiSimulationSetups["simulation:matrix"][\'session]'
        # A saved corner-only section change must be rejected, despite unchanged tests.
        bridge.skill(
            "let((m) m=axlGetModel(axlGetCorner(axlGetMainSetupDB(" + sid + ') "tt") "model_1") '
            'axlSetModelSection(m "ff") maeSaveSetup(?session ' + sid + "))"
        )
        assert not mcp_call(server, "inspect_simulation_setup", dict(setup_ref=setup["setup_ref"]))[
            "ok"
        ]
        denied = mcp_call(
            server, "run_simulation_setup", dict(setup_ref=setup["setup_ref"], request_id="changed")
        )
        assert denied["state"] == "failed" and denied["history"] is None, denied
        bridge.skill(
            "let((m) m=axlGetModel(axlGetCorner(axlGetMainSetupDB(" + sid + ') "tt") "model_1") '
            'axlSetModelSection(m "tt") maeSaveSetup(?session ' + sid + "))"
        )
        assert mcp_call(server, "inspect_simulation_setup", dict(setup_ref=setup["setup_ref"]))[
            "ok"
        ]
        for changed, restored in [("2000 5000", "2000 4000")]:
            bridge.skill(
                'progn(maeSetVar("RVAL" ' + json.dumps(changed) + " ?session " + sid + ") "
                "maeSaveSetup(?session " + sid + "))"
            )
            assert not mcp_call(
                server, "inspect_simulation_setup", dict(setup_ref=setup["setup_ref"])
            )["ok"]
            bridge.skill(
                'progn(maeSetVar("RVAL" ' + json.dumps(restored) + " ?session " + sid + ") "
                "maeSaveSetup(?session " + sid + "))"
            )
        for enabled in ("nil", "t"):
            bridge.skill(
                "let((v) v=axlGetVar(axlGetCorner(axlGetMainSetupDB(" + sid + ') "tt") "OFFSET") '
                "axlSetEnabled(v " + enabled + ") maeSaveSetup(?session " + sid + "))"
            )
            check = mcp_call(server, "inspect_simulation_setup", dict(setup_ref=setup["setup_ref"]))
            assert check["ok"] == (enabled == "t"), check
        (tmp_path / "acceptance.json").write_text(
            json.dumps(
                dict(preview=preview, status=status, netlist_coordinates=sorted(coords)), indent=2
            )
        )


@pytest.mark.skipif(
    os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1", reason="licensed dimension modes"
)
@pytest.mark.parametrize(
    "mode", ["corner_only", "sweep_only", "bad_section", "corner_empty", "corner_plain"]
)
def test_independent_dimension_modes(tmp_path, mode):
    with session(tmp_path) as (bridge, server, _):
        load_backend(bridge, tmp_path)
        source_fixture(bridge, server)
        model = tmp_path / "m.scs"
        model.write_text(
            "simulator lang=spectre\nlibrary fixture\nsection tt\n"
            "endsection tt\nendlibrary fixture\n"
        )
        data = dimension_recipe(model, cell=mode)
        data["tests"] = data["tests"][1:]
        data["corners"] = data["corners"][:1]
        data["sweeps"] = data["sweeps"][:1]
        if mode in ("corner_only", "corner_empty", "corner_plain"):
            data.pop("sweeps")
        elif mode == "sweep_only":
            data.pop("corners")
        else:
            data.pop("sweeps")
            data["corners"][0]["models"][0]["section"] = "ABSENT"
        if mode == "corner_empty":
            data["corners"][0]["models"] = []
        elif mode == "corner_plain":
            model.write_text("simulator lang=spectre\nparameters plainProof=1\n")
            data["corners"][0]["models"] = [dict(path=str(model))]
        pre = mcp_call(server, "preview_simulation_recipe", dict(recipe=data))
        made = mcp_call(
            server,
            "create_simulation_setup",
            dict(
                recipe=data,
                recipe_digest=pre["recipe_digest"],
                task_ref="task:source",
                request_id=mode,
            ),
        )
        assert made["ok"], made
        run = mcp_call(
            server, "run_simulation_setup", dict(setup_ref=made["setup_ref"], request_id=mode)
        )
        status = terminal(server, run)
        (tmp_path / "status.json").write_text(json.dumps(status, indent=2))
        raw = bridge.skill(
            'let((s d) s=aiSimulationSetups["simulation:' + mode + "\"]['session] "
            'd=maeReadResDB(?session s ?historyName "' + run["history"] + '") '
            "aiCircuitJson(foreach(mapcar p d->points() "
            "foreach(mapcar c p->corners() foreach(mapcar v c->params() list(v->name v->value))))))"
        )
        (tmp_path / "raw-coordinates.json").write_text(raw)
        assert status["state"] == ("failed" if mode == "bad_section" else "completed"), status
        (tmp_path / "acceptance.json").write_text(json.dumps(status, indent=2))
