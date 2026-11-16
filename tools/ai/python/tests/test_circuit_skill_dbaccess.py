"""Real SKILL interpreter test, including DD lookup and resource ownership."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

AI_ROOT = Path(__file__).resolve().parents[2]


def test_circuit_context_roundtrip_dbaccess(tmp_path):
    executable = shutil.which(os.environ.get("CAD_AI_DBACCESS", "dbAccess"))
    if executable is None:
        pytest.skip("dbAccess unavailable")
    lines = [f'load({json.dumps(str(AI_ROOT / "skill" / name))})' for name in
             ("AI_protocol.il", "AI_circuit.il", "AI_circuit_context.il")]
    lib_path = tmp_path / "reference"
    lines += [f'ddCreateLib("circuitProbe" {json.dumps(str(lib_path))})',
              'cv=dbOpenCellViewByType("circuitProbe" "one" "symbol" "schematicSymbol" "w")',
              'dbSave(cv)', 'dbClose(cv)',
              'procedure(probeWrite(name value) let((port) '
              f'port=outfile(strcat({json.dumps(str(tmp_path))} "/" name ".json")) '
              'fprintf(port "%s\\n" value) close(port)))',
              'probeWrite("missing" aiCircuitOpen("circuitProbe" "missing" "symbol" "schematicSymbol" "background" "read" "miss" "design:miss"))',
              'probeWrite("type" aiCircuitOpen("circuitProbe" "one" "symbol" "schematic" "background" "read" "type" "design:type"))',
              'probeWrite("preflight" aiCircuitPreflight("circuitProbe" "one" "symbol" "schematicSymbol" "background"))',
              'probeWrite("open" aiCircuitOpen("circuitProbe" "one" "symbol" "schematicSymbol" "background" "edit" "one" "design:one"))',
              'probeWrite("repeat" aiCircuitOpen("circuitProbe" "one" "symbol" "schematicSymbol" "background" "edit" "one" "design:ignored"))',
              'probeWrite("conflict" aiCircuitOpen("circuitProbe" "one" "symbol" "schematicSymbol" "background" "read" "one" "design:conflict"))',
              'probeWrite("inspect" aiCircuitInspect("design:one"))',
              'boundCv=aiCircuitContexts["design:one"][\'cv]',
              'dbCreateLabel(boundCv list("text" "drawing") 0:0 "unsaved" "lowerLeft" "R0" "stick" 0.1)',
              'probeWrite("modified" aiCircuitInspect("design:one"))',
              'probeWrite("retain" aiCircuitRelease("design:one"))',
              'dbSave(boundCv)',
              'probeWrite("release" aiCircuitRelease("design:one"))',
              'probeWrite("released" aiCircuitInspect("design:one"))',
              'probeWrite("release_again" aiCircuitRelease("design:one"))',
              'unless(!ddGetObj("circuitProbe" "missing") error("missing target created"))',
              'printf("CIRCUIT_DBACCESS_OK\\n")', 'exit()']
    result = subprocess.run([executable], input="\n".join(lines), text=True, capture_output=True,
                            cwd=tmp_path, timeout=45)
    output = result.stdout + result.stderr
    assert result.returncode == 0 and "CIRCUIT_DBACCESS_OK" in output, output
    read = lambda name: json.loads((tmp_path / (name + ".json")).read_text())
    assert read("missing")["code"] == "view_not_found"
    assert read("type")["code"] == "oa_view_type_mismatch"
    assert read("preflight")["ready_to_open"] is True
    assert read("preflight")["simulation_qualified"] is False
    assert read("open")["ownership"] == "owned_background"
    assert read("repeat")["context_ref"] == "design:one"
    assert read("conflict")["code"] == "request_conflict"
    assert read("inspect")["modified"] is False
    assert read("modified")["modified"] is True
    assert read("retain")["code"] == "release_failed"
    assert read("release")["stage"] == "released"
    assert read("released")["code"] == "released"
    assert read("release_again")["stage"] == "released"
