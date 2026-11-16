"""Native failure retention, stale source preflight and exact-history cancellation."""

import json
import os
import time

import pytest
from circuit_create_support import session
from simulation_recipe_fixture import recipe
from test_circuit_rc_gui import mcp_call
from test_simulation_recipe_gui import load_backend, source_fixture

pytestmark = pytest.mark.skipif(
    os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1", reason="licensed lifecycle failure acceptance"
)


def prepare(server, name, data=None):
    data = data or recipe(cell=name)
    preview = mcp_call(server, "preview_simulation_recipe", {"recipe": data})
    args = dict(
        recipe=data, recipe_digest=preview["recipe_digest"], task_ref="task:source", request_id=name
    )
    return mcp_call(server, "create_simulation_setup", args), args


def terminal(server, run):
    end = time.monotonic() + 160
    while time.monotonic() < end:
        status = mcp_call(server, "get_simulation_recipe_status", {"run_ref": run["run_ref"]})
        if status["state"] not in ("running", "stop_requested"):
            return status
        time.sleep(0.5)
    pytest.fail("Simulation did not reach terminal state: " + repr(status))


def test_stale_source_and_partial_creation_are_retained(tmp_path):
    with session(tmp_path) as (bridge, server, _):
        load_backend(bridge, tmp_path)
        source_fixture(bridge, server)
        bridge.skill(
            'let((cv) cv=dbOpenCellViewByType("genericWork" "network" "schematic" "" "a") '
            'dbReplaceProp(dbFindAnyInstByName(cv "R1") "r" "string" "3K") '
            "dbSave(cv) dbClose(cv))"
        )
        stale, _ = prepare(server, "stale")
        assert not stale["ok"] and stale["stage"] == "preflight", stale
        assert "Check & Save" in stale["error"]
        assert bridge.skill('ddGetObj("genericWork" "stale" "maestro")') == "nil"
        bridge.skill(
            'let((cv) cv=dbOpenCellViewByType("genericWork" "network" "schematic" "" "a") '
            "schCheck(cv) dbSave(cv) dbClose(cv))"
        )
        # Inject failure after maeOpenSetup; retain the partial native session and receipt.
        bridge.skill(
            "progn(testSavedConfigure=getd('aiSimConfigure) "
            'procedure(aiSimConfigure(testEntry) error("injected configuration failure")) t)'
        )
        try:
            partial, args = prepare(server, "partial")
        finally:
            bridge.skill("putd('aiSimConfigure testSavedConfigure)")
        assert not partial["ok"] and partial["stage"] == "open_setup", partial
        assert not partial["saved"]
        assert mcp_call(server, "create_simulation_setup", args) == partial
        assert (
            mcp_call(server, "release_simulation_setup", {"setup_ref": partial["setup_ref"]})[
                "code"
            ]
            == "partial_setup_retained"
        )
        assert (
            bridge.skill(
                'member(aiSimulationSetups["simulation:partial"][\'session] maeGetSessions())'
            )
            != "nil"
        )
        # Reopen can fail after the saved target exists but before session is retained.
        bridge.skill(
            "progn(testSavedPlace=getd('aiCircuitPlaceRightHalf) "
            'procedure(aiCircuitPlaceRightHalf(testWindow) error("injected reopen failure")) t)'
        )
        try:
            reopened, reopened_args = prepare(server, "reopen_failed")
        finally:
            bridge.skill("putd('aiCircuitPlaceRightHalf testSavedPlace)")
        assert not reopened["ok"] and reopened["saved"] and reopened["stage"] == "reopen", reopened
        assert "inspect before retrying" in reopened["error"], reopened
        assert "target not opened" not in reopened["error"], reopened
        assert bridge.skill('ddGetObj("genericWork" "reopen_failed" "maestro")') != "nil"
        assert bridge.skill('aiSimulationSetups["simulation:reopen_failed"][\'session]') == "nil"
        assert mcp_call(server, "create_simulation_setup", reopened_args) == reopened
        (tmp_path / "failures.json").write_text(
            json.dumps(dict(stale=stale, partial=partial, reopened=reopened), indent=2)
        )


def test_failed_simulation_and_stop_do_not_report_pass(tmp_path):
    with session(tmp_path) as (bridge, server, _):
        load_backend(bridge, tmp_path)
        source_fixture(bridge, server)
        data = recipe(cell="bad_parameter")
        data["variables"]["RVAL"] = "MISSING_PARAMETER"
        setup, _ = prepare(server, "bad_parameter", data)
        assert setup["ok"], setup
        run_args = dict(setup_ref=setup["setup_ref"], request_id="bad_parameter")
        failed = terminal(server, mcp_call(server, "run_simulation_setup", run_args))
        assert failed["state"] == "failed" and failed["history"], failed
        assert not failed["spec_qualified"]
        assert any(row[-1] == "failed" for row in failed["test_states"])
        assert mcp_call(server, "run_simulation_setup", run_args) == failed
        assert mcp_call(server, "release_simulation_setup", {"setup_ref": setup["setup_ref"]})[
            "released"
        ]
        # A long but bounded native TRAN is cancelled immediately after its history is allocated.
        data = recipe(cell="cancelled")
        data["tests"] = data["tests"][:1]
        data["tests"][0]["analyses"][0]["options"].update(stop="1m", maxstep="1n")
        setup, _ = prepare(server, "cancelled", data)
        assert setup["ok"], setup
        run_args = dict(setup_ref=setup["setup_ref"], request_id="cancelled")
        running = mcp_call(server, "run_simulation_setup", run_args)
        assert running["state"] == "running", running
        stop = mcp_call(server, "stop_simulation_recipe", {"run_ref": running["run_ref"]})
        assert stop["state"] == "stop_requested", stop
        assert mcp_call(server, "stop_simulation_recipe", {"run_ref": running["run_ref"]}) == stop
        stopped = terminal(server, running)
        assert stopped["state"] == "stopped" and not stopped["spec_qualified"], stopped
        assert mcp_call(server, "run_simulation_setup", run_args) == stopped
        assert mcp_call(server, "release_simulation_setup", {"setup_ref": setup["setup_ref"]})[
            "released"
        ]
        (tmp_path / "run-failures.json").write_text(
            json.dumps(dict(failed=failed, stopped=stopped), indent=2)
        )
