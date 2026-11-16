"""Licensed RC MCP -> controller -> native SKILL -> Spectre/RDB acceptance."""
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import time
import uuid

import pytest

from cadai.controller import RequestDispatcher
from cadai.headless_session import (  # noqa: F401  (re-export for test fixtures)
    AI_ROOT,
    IC_LIBS,
    DispatchClient,
    NativeBridge,
    mcp_call,
)
from cadai.mcp import McpServer
from cadai.runtime import RuntimePaths


def test_rc_foreground_and_background_closed_loop(tmp_path):
    if os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1":
        pytest.skip("set CAD_AI_RUN_GUI_TESTS=1 for licensed GUI/Spectre acceptance")
    virtuoso = shutil.which(os.environ.get("CAD_AI_VIRTUOSO", "virtuoso"))
    xvfb = shutil.which("Xvfb")
    if not virtuoso or not xvfb:
        pytest.skip("Virtuoso/Xvfb unavailable")
    library = "rcMcp" + uuid.uuid4().hex[:8]
    (tmp_path / "cds.lib").write_text(f"DEFINE analogLib {IC_LIBS}/artist/analogLib\nDEFINE basic {IC_LIBS}/basic\n")
    modules = ["AI_protocol.il", "AI_eval.il", "AI_circuit.il", "AI_circuit_context.il",
               "AI_circuit_task.il", "AI_circuit_rc.il", "AI_circuit_rc_maestro.il", "AI_circuit_rc_run.il"]
    lines = [f'load({json.dumps(str(AI_ROOT / "skill" / name))})' for name in modules]
    lines += [f'ddCreateLib("{library}" {json.dumps(str(tmp_path / "library"))})',
              # Local scratch results, no site-global environment edits.
              f'envSetVal("asimenv.startup" "projectDir" \'string {json.dumps(str(tmp_path / "simulation"))})',
              'procedure(rcTestPoll() when(isFile("command.il") load("command.il") deleteFile("command.il")) '
              'hiRegTimer("rcTestPoll()" 1))', 'rcTestPoll()', 'aiWriteTextFile("ready" "yes")']
    (tmp_path/"run.il").write_text("\n".join(lines)+"\n")
    read_fd, write_fd = os.pipe()
    xprocess = subprocess.Popen([xvfb, "-displayfd", str(write_fd), "-screen", "0", "1440x1000x24", "-nolisten", "tcp"],
                                pass_fds=(write_fd,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.close(write_fd)
    process = None
    try:
        assert select.select([read_fd], [], [], 10)[0]
        display = os.read(read_fd, 32).decode().strip()
        assert display.isdigit()
        with (tmp_path/"process.log").open("w") as log:
            process = subprocess.Popen([virtuoso, "-nocdsinit", "-norestore", "-replay", "run.il", "-log", "CDS.log"],
                                       cwd=tmp_path, env=dict(os.environ, DISPLAY=":"+display),
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        bridge = NativeBridge(tmp_path, process)
        bridge.wait_file(tmp_path/"ready")
        spool = tmp_path/"spool"
        spool.mkdir(mode=0o700)
        runtime = RuntimePaths(tmp_path, tmp_path/"socket", spool)
        server = McpServer(DispatchClient(RequestDispatcher(bridge, runtime, tmp_path)))
        all_results = []
        for mode in ("foreground", "background"):
            task = mcp_call(server, "begin_circuit_task", {"task_id": mode, "presentation": mode, "choice_confirmed": True})
            assert task["presentation"] == mode
            conflict = mcp_call(server, "begin_circuit_task", {"task_id": mode,
                "presentation": "background" if mode == "foreground" else "foreground", "choice_confirmed": True})
            assert conflict["code"] == "task_mode_conflict"
            spec = {"library": library, "cell": mode}
            preview = mcp_call(server, "preview_rc_circuit", spec)
            args = {**spec, "task_ref": task["task_ref"], "request_id": mode, "preview_digest": preview["preview_digest"]}
            created = mcp_call(server, "create_rc_circuit", args)
            assert created["saved"] and created["check"] == [0, 0], created
            assert created == mcp_call(server, "create_rc_circuit", args)
            rejected = mcp_call(server, "create_rc_circuit", {**args, "request_id": mode+"-exists"})
            assert rejected["ok"] is False and rejected["stage"] == "preflight"
            setup_args = {"circuit_ref": created["circuit_ref"], "request_id": mode}
            setup = mcp_call(server, "create_rc_maestro", setup_args)
            assert setup["maestro_saved"], setup
            assert setup == mcp_call(server, "create_rc_maestro", setup_args)
            run = mcp_call(server, "run_rc_simulation", setup_args)
            assert run["state"] == "running", run
            repeat = mcp_call(server, "run_rc_simulation", setup_args)
            assert repeat["run_ref"] == run["run_ref"] and repeat["history"] == run["history"]
            deadline = time.monotonic()+100
            while time.monotonic() < deadline:
                status = mcp_call(server, "get_rc_run_status", {"run_ref": run["run_ref"]})
                if status["state"] != "running":
                    break
                time.sleep(0.5)
            assert status["state"] == "completed", status
            results = mcp_call(server, "read_rc_results", {"run_ref": run["run_ref"]})
            assert results["passed"] and len(results["rows"]) == 2, results
            assert results["rows"][0][1] == pytest.approx(1.0, abs=0.002)
            assert results["rows"][1][1] == pytest.approx(preview["expected"]["tran_tau_v"], abs=0.002)
            all_results.append(results)
            if mode == "foreground":
                # Unsaved user changes must reject without being checked or saved by the tool.
                bridge.skill('dbReplaceProp(aiRcCircuits["rc:foreground"][\'cv] "testUserEdit" "string" "unsaved")')
                denied = mcp_call(server, "run_rc_simulation", {**setup_args, "request_id": "unsaved"})
                assert denied["state"] == "failed"
                # Test owns this edit and cleans it up; the product only refuses it.
                bridge.skill('let((cv) cv=aiRcCircuits["rc:foreground"][\'cv] schCheck(cv) dbSave(cv))')
            else:
                # Saved wire edits must reject even when old extracted connectivity is cached.
                bridge.skill('let((cv) cv=dbOpenCellViewByType(' + json.dumps(library) +
                             ' "background" "schematic" "schematic" "a") '
                             'schCreateWire(cv "draw" "full" list(5:0 5:1) 0.0625 0.0625 0.0) schCheck(cv) dbSave(cv) dbClose(cv))')
                denied = mcp_call(server, "run_rc_simulation", {**setup_args, "request_id": "geometry"})
                assert denied["state"] == "failed"
                assert "schematic geometry changed" in denied["error"], denied
            released = mcp_call(server, "release_rc_circuit", {"circuit_ref": created["circuit_ref"]})
            assert released["stage"] == "released", released
            if mode == "foreground":
                bridge.skill('unless(and(member(aiRcCircuits["rc:foreground"][\'window] hiGetWindowList(\'all)) '
                             'member(aiRcCircuits["rc:foreground"][\'maestroWindow] hiGetWindowList(\'all))) '
                             'error("release closed GUI"))')
            else:
                assert bridge.skill('member(' + json.dumps(setup["maestro_session"]) + ' maeGetSessions())') == "nil"
        (tmp_path/"acceptance.json").write_text(json.dumps(all_results, indent=2))
        # Save only this test's own GUI setup normalization before exiting its process.
        bridge.skill('progn(foreach(testSession maeGetSessions() maeSaveSetup(?session testSession)) '
                     'foreach(testCv dbGetOpenCellViews() '
                     'when(testCv~>libName==' + json.dumps(library) +
                     ' && testCv~>mode!="r" dbSave(testCv))))')
        bridge.skill('hiRegTimer("exit()" 5)')
        assert process.wait(timeout=25) == 0
    finally:
        os.close(read_fd)
        if process and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        xprocess.terminate()
        xprocess.wait(timeout=5)
