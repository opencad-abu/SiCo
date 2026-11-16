"""The authenticated ADE run directory expands `~` like ADE itself does.

`asimenv.startup/projectDir` defaults to the literal `~/simulation`; ADE expands
it when it computes the run area, so the preflight and final gates expand it from
the Virtuoso environment as well. `~user` spellings stay rejected.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

AI_ROOT = Path(__file__).resolve().parents[2]


def _skill_string(value) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def test_run_simulation_setup_expands_home_project_dir(tmp_path: Path) -> None:
    dbaccess = shutil.which(os.environ.get("CAD_AI_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")

    home = tmp_path / "home"
    home.mkdir()
    library = tmp_path / "probeLib"
    library.mkdir()
    (tmp_path / "cds.lib").write_text(f"DEFINE probeLib {library}\n")
    root = _skill_string(tmp_path)
    loader = AI_ROOT.parent / "sico/skill++/SICO.ils"
    lines = [
        f"load({_skill_string(loader)})",
        "procedure(probeWrite(pName pValue) let((pPort) "
        f'pPort=outfile(strcat({root} "/" pName ".json")) '
        'fprintf(pPort "%s\\n" pValue) close(pPort)))',
        "procedure(probeCapture(pName) let((pFlag) "
        'pFlag=errset(aiCopilotDescribe("run_simulation_setup" "simulation:probe" nil nil) nil) '
        'probeWrite(pName if(pFlag car(pFlag) sprintf(nil "%L" errset.errset)))))',
        'sicoNativeIdentity=list("session" "lifetime" "instance" "generation" '
        f"{root} \"task:probe\" \"0123456789abcdef0123456789abcdef\" nil)",
        'aiCopilotSource=list("probe-source")',
        'aiCopilotTasks["task:probe"]=aiCopilotOwner(nil)',
        'aiCircuitTasks["task:probe"]="background"',
        'probeEntry=makeTable("aiSimulationSetup" nil)',
        "probeEntry['task]=\"task:probe\" probeEntry['digest]=\"digest\"",
        'probeEntry[\'payload]=list(list("probeLib" "probeCell" "maestro") nil "27" nil nil)',
        "probeEntry['models]=nil",
        'aiSimulationSetups["simulation:probe"]=probeEntry',
        'envSetVal("asimenv.startup" "projectDir" \'string "~/simulation")',
        'probeCapture("home")',
        'envSetVal("asimenv.startup" "projectDir" \'string "~")',
        'probeCapture("bare")',
        'envSetVal("asimenv.startup" "projectDir" \'string "~other/simulation")',
        'probeCapture("other")',
        'envSetVal("asimenv.startup" "projectDir" \'string "relative/dir")',
        'probeCapture("relative")',
        "exit()",
    ]
    result = subprocess.run(
        [dbaccess],
        input="\n".join(lines),
        text=True,
        capture_output=True,
        cwd=tmp_path,
        env={**os.environ, "HOME": str(home), "SICO_ROOT": str(AI_ROOT)},
        timeout=60,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output

    def read(name):
        return (tmp_path / f"{name}.json").read_text()

    raw = read("home")
    assert raw.startswith("{") and "simulation_project_dir_invalid" not in raw, raw
    described = json.loads(raw)
    assert described["binding"][6] == str(home / "simulation")
    assert described["binding"][5] == str(library)
    assert json.loads(read("bare"))["binding"][6] == str(home)
    assert "~other/simulation" in read("other") and "projectDir" in read("other")
    assert "relative/dir" in read("relative")
