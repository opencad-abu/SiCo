"""Opt-in real Virtuoso/Xvfb acceptance for explicit schematic/Maestro contexts."""
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess

import pytest

AI_ROOT = Path(__file__).resolve().parents[2]


def test_foreground_and_background_maestro_contexts(tmp_path):
    if os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1":
        pytest.skip("set CAD_AI_RUN_GUI_TESTS=1 for licensed GUI acceptance")
    virtuoso = shutil.which(os.environ.get("CAD_AI_VIRTUOSO", "virtuoso"))
    xvfb = shutil.which("Xvfb")
    if not virtuoso or not xvfb:
        pytest.skip("Virtuoso/Xvfb unavailable")
    lines = [f'load({json.dumps(str(AI_ROOT / "skill" / n))})' for n in
             ("AI_protocol.il", "AI_circuit.il", "AI_circuit_context.il")]
    lines += [
        'procedure(guiWrite(name value) let((port) '
        f'port=outfile(strcat({json.dumps(str(tmp_path))} "/" name ".json")) '
        'fprintf(port "%s\\n" value) close(port)))',
        f'ddCreateLib("circuitGui" {json.dumps(str(tmp_path / "library"))})',
        'cv=dbOpenCellViewByType("circuitGui" "tb" "schematic" "schematic" "w")',
        'dbSave(cv)', 'dbClose(cv)',
        'ses=maeOpenSetup("circuitGui" "tb" "maestro" ?application "Assembler" ?mode "a")',
        'maeCreateTest("TRAN" ?lib "circuitGui" ?cell "tb" ?view "schematic" ?simulator "spectre" ?session ses)',
        'maeSaveSetup(?session ses)', 'maeCloseSession(?session ses)',
        'guiWrite("background" aiCircuitOpen("circuitGui" "tb" "maestro" "maestro" "background" "read" "bg" "design:bg"))',
        'guiWrite("background_release" aiCircuitRelease("design:bg"))',
        'guiWrite("maestro" aiCircuitOpen("circuitGui" "tb" "maestro" "maestro" "foreground" "edit" "mae" "design:mae"))',
        'guiWrite("schematic" aiCircuitOpen("circuitGui" "tb" "schematic" "schematic" "foreground" "edit" "sch" "design:sch"))',
        'guiWrite("maestro_after_focus" aiCircuitInspect("design:mae"))',
        'guiWrite("rebind" aiCircuitOpen("circuitGui" "tb" "schematic" "schematic" "foreground" "edit" "again" "design:again"))',
        'guiWrite("repeat" aiCircuitOpen("circuitGui" "tb" "schematic" "schematic" "foreground" "edit" "sch" "design:ignored"))',
        'maeWindow=aiCircuitContexts["design:mae"][\'window]',
        'maeSession=aiCircuitContexts["design:mae"][\'session]',
        # Test owns the isolated setup. Save runtime's own setup normalization before exit.
        'maeSaveSetup(?session maeSession)',
        'guiWrite("maestro_release" aiCircuitRelease("design:mae"))',
        'unless(member(maeWindow hiGetWindowList(\'all)) error("release closed GUI"))',
        'unless(member(maeSession maeGetSessions()) error("release closed GUI session"))',
        'schWindow=aiCircuitContexts["design:sch"][\'window]',
        'hiCloseWindow(schWindow)',
        'guiWrite("closed" aiCircuitInspect("design:sch"))',
        'guiWrite("closed_again" aiCircuitInspect("design:again"))',
        'printf("CIRCUIT_GUI_ACCEPTANCE_OK\\n")', 'hiRegTimer("exit()" 5)',
    ]
    (tmp_path / "run.il").write_text("\n".join(lines) + "\n")
    (tmp_path / "cds.lib").write_text("")
    read_fd, write_fd = os.pipe()
    xprocess = subprocess.Popen([xvfb, "-displayfd", str(write_fd), "-screen", "0", "1280x900x24", "-nolisten", "tcp"],
                                pass_fds=(write_fd,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.close(write_fd)
    process = None
    try:
        assert select.select([read_fd], [], [], 10)[0], "Xvfb did not publish a display"
        display = os.read(read_fd, 32).decode().strip()
        assert display.isdigit()
        env = dict(os.environ, DISPLAY=":" + display)
        with (tmp_path / "process.log").open("w") as log:
            process = subprocess.Popen([virtuoso, "-nocdsinit", "-norestore", "-replay", "run.il", "-log", "CDS.log"],
                                       cwd=tmp_path, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            assert process.wait(timeout=75) == 0
    finally:
        os.close(read_fd)
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        xprocess.terminate()
        xprocess.wait(timeout=5)
    output = (tmp_path / "CDS.log").read_text(errors="replace")
    assert "CIRCUIT_GUI_ACCEPTANCE_OK" in output and "*Error*" not in output, output[-10000:]
    read = lambda name: json.loads((tmp_path / (name + ".json")).read_text())
    assert read("background")["effective_access"] == "read"
    assert read("background_release")["stage"] == "released"
    assert read("maestro")["session"] == read("maestro_after_focus")["session"]
    assert read("maestro_release")["window_open"] is True
    assert read("schematic")["effective_presentation"] == "foreground"
    assert read("repeat")["context_ref"] == "design:sch"
    assert read("rebind")["ok"] is True
    assert read("closed")["code"] == read("closed_again")["code"] == "context_changed"
