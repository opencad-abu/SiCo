from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from form_lifecycle_fixtures import RUN_PROBE, _install_tree, _skill_literal
from skill_probe_support import run_virtuoso_source


@pytest.mark.skipif(
    not RUN_PROBE,
    reason="set RCE_RUN_SKILL_PROBE=1 to run the Virtuoso lifecycle probe",
)
@pytest.mark.parametrize(
    ("entry", "display", "form_name", "flow"),
    (
        ("drc/skill++/DRC.ils", "cadDisplayDrcForm", "drcForm", "DRC"),
        ("lvs/skill++/LVS.ils", "cadDisplayLvsForm", "lvsForm", "LVS"),
        ("rce/skill++/RCE.ils", "cadDisplayRceForm", "rceForm", "RCE"),
        (
            "lvs/skill++/LVS.ils",
            "cadDisplayStreamGdsForm",
            "streamGdsForm",
            "STREAM_GDS",
        ),
        (
            "lvs/skill++/LVS.ils",
            "cadDisplayExportCdlForm",
            "exportCdlForm",
            "EXPORT_CDL",
        ),
    ),
)
def test_flow_form_close_reopen_reuses_live_form(
    tmp_path: Path, entry: str, display: str, form_name: str, flow: str
) -> None:
    virtuoso = shutil.which(os.environ.get("RCE_VIRTUOSO", "virtuoso"))
    if virtuoso is None:
        pytest.skip("virtuoso is unavailable")
    install = _install_tree(tmp_path)
    replay = tmp_path / f"{flow.lower()}-lifecycle.il"
    log = tmp_path / f"{flow.lower()}-lifecycle.log"
    replay.write_text(
        "\n".join(
                (
                    f'setShellEnvVar("CAD_HOME" {_skill_literal(install)})',
                    f'load({_skill_literal(install / "tools" / entry)})',
                    f"first={display}()",
                    "firstValid=and(first hiIsForm(first) hiIsFormDisplayed(first))",
                    "hiFormClose(first)",
                    "closedOk=and(hiIsForm(first) !hiIsFormDisplayed(first))",
                    f"second={display}()",
                    "reopenedOk=and(second hiIsForm(second) hiIsFormDisplayed(second) second==first)",
                "when(\n"
                f'  and(firstValid closedOk reopenedOk) printf("{flow}_FORM_LIFECYCLE_OK\\n"))',
                "when(hiIsForm(second) hiFormClose(second) hiDeleteForm(second))",
                "exit()",
                "",
            )
        ),
        encoding="utf-8",
    )
    output = run_virtuoso_source(replay.read_text(), tmp_path,
        env_updates={'DRC_DB_DIR': str(tmp_path), 'LVS_DB_DIR': str(tmp_path), 'RCE_DB_DIR': str(tmp_path)}, log_path=log)
    assert "*Error*" not in output
    assert "(reader)" not in output
    assert "still unclosed on EOF" not in output
    assert f"{flow}_FORM_LIFECYCLE_OK" in output
