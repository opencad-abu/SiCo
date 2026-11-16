"""Licensed circuit window presentation contracts: opened windows reach the user."""

import os

import pytest
from circuit_create_fixture import fixture
from circuit_create_support import sample_master, session
from test_circuit_rc_gui import mcp_call


@pytest.mark.skipif(os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1", reason="licensed GUI acceptance")
def test_foreground_create_raises_the_opened_window(tmp_path):
    with session(tmp_path) as (bridge, server, process):
        data = {c: sample_master(bridge, c) for c in ("res", "cap")}
        # Record presentation requests instead of restacking the real window list.
        bridge.skill("progn(aiTestRaiseCount=0 aiTestRaised=nil "
                     "procedure(aiCircuitRaiseWindow(testWindow) "
                     "aiTestRaiseCount=aiTestRaiseCount+1 aiTestRaised=testWindow t) t)")
        for mode in ("foreground", "background"):
            task = mcp_call(server, "begin_circuit_task",
                            dict(task_id=mode, presentation=mode, choice_confirmed=True))
            args = {**fixture(data, mode), "task_ref": task["task_ref"], "request_id": mode}
            prepared = mcp_call(server, "prepare_circuit_creation", args)
            assert prepared["ok"] and prepared["stage"] == "prepared", prepared
            created = mcp_call(server, "create_circuit_from_plan", dict(
                prepare_ref=prepared["prepare_ref"],
                geometry_plan_digest=args["geometry_plan_digest"], request_id=mode))
            assert created["ok"] and created["window_open"] == (mode == "foreground"), created
            if mode == "foreground":
                assert bridge.skill("aiTestRaiseCount==1") == "t"
                assert bridge.skill(
                    'and(windowp(aiTestRaised) eq(hiGetWindowState(aiTestRaised) \'mapped) '
                    'geGetWindowCellView(aiTestRaised)~>libName=="genericWork" '
                    'geGetWindowCellView(aiTestRaised)~>cellName=="foreground")') == "t"
            else:
                assert bridge.skill("and(aiTestRaiseCount==0 aiTestRaised==nil)") == "t"
            bridge.skill("progn(aiTestRaiseCount=0 aiTestRaised=nil t)")
