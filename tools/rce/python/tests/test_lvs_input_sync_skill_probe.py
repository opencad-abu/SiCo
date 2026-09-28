from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


CAD_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.skipif(
    os.environ.get("RCE_RUN_SKILL_PROBE") != "1",
    reason="set RCE_RUN_SKILL_PROBE=1 to run the Cadence Virtuoso probe",
)
def test_lvs_oa_input_sync_buttons_and_callbacks(tmp_path: Path) -> None:
    virtuoso = shutil.which(os.environ.get("RCE_VIRTUOSO", "virtuoso"))
    if virtuoso is None:
        pytest.skip("virtuoso is unavailable")

    install = tmp_path / "install"
    install.mkdir()
    (install / "tools").symlink_to(CAD_ROOT, target_is_directory=True)
    replay = tmp_path / "lvs_input_sync.il"
    log = tmp_path / "lvs_input_sync.log"
    replay.write_text(
        "\n".join(
            (
                f'setShellEnvVar("CAD_HOME" "{install}")',
                f'load("{install}/tools/lvs/skill++/LVS.ils")',
                "cadDisplayLvsForm()",
                "form=lvsForm",
                'form~>inpType~>value="OA"',
                "lvsInpCB(form)",
                "oaVisible=!form~>inputSync~>invisible",
                'form~>schLib~>value="schematicLib"',
                'form~>schCell~>value="schematicCell"',
                'form~>layView~>value="layoutView"',
                'lvsSyncInputNames(form "Schematic")',
                'forwardOk=and(form~>layLib~>value=="schematicLib" '
                'form~>layCell~>value=="schematicCell" '
                'form~>layView~>value=="layoutView")',
                'form~>layLib~>value="layoutLib"',
                'form~>layCell~>value="layoutCell"',
                'form~>schView~>value="schematicView"',
                'lvsSyncInputNames(form "Layout")',
                'reverseOk=and(form~>schLib~>value=="layoutLib" '
                'form~>schCell~>value=="layoutCell" '
                'form~>schView~>value=="schematicView")',
                'form~>inpType~>value="CDL+GDS"',
                "lvsInpCB(form)",
                "nonOaHidden=form~>inputSync~>invisible",
                "when(form hiFormDone(form) hiDeleteForm(form))",
                "if(and(oaVisible forwardOk reverseOk nonOaHidden) "
                'then printf("LVS_INPUT_SYNC_PROBE_OK\\n"))',
                "exit()",
                "",
            )
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [virtuoso, "-nograph", "-nocdsinit", "-replay", str(replay), "-log", str(log)],
        cwd=tmp_path,
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )
    output = completed.stdout + completed.stderr
    if log.is_file():
        output += log.read_text(encoding="utf-8", errors="replace")
    assert completed.returncode == 0, output
    assert "*Error*" not in output
    assert "(reader)" not in output
    assert "LVS_INPUT_SYNC_PROBE_OK" in output
