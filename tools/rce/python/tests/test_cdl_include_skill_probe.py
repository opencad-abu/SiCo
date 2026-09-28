from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from skill_test_support import common_source_loads

CAD_ROOT = Path(__file__).resolve().parents[3]


def _virtuoso() -> str:
    executable = shutil.which(os.environ.get("RCE_VIRTUOSO", "virtuoso"))
    if executable is None:
        pytest.skip("virtuoso is unavailable")
    return executable


@pytest.mark.skipif(
    os.environ.get("RCE_RUN_SKILL_PROBE") != "1",
    reason="set RCE_RUN_SKILL_PROBE=1 to run the Cadence Virtuoso probe",
)
def test_cdl_include_form_defaults_and_input_visibility() -> None:
    virtuoso = _virtuoso()

    toml_helper = CAD_ROOT / "common/skill/SICO_toml.il"
    skill = "\n".join(
        (
            f'load("{toml_helper}")',
            common_source_loads("gui"),
            "gui=makeInstance(quote(INPUTGUI))",
            'gui->inpChoices=list("OA" "SCH+GDS" "CDL+GDS" "CDL+LAY" "SVDB" "CCI")',
            "makeInpType(gui)",
            "makeCdlInclude(gui)",
            "layout=makevbl(quote(cdlIncludeProbeLayout) "
            "list(gui->inpType gui->cdlInclude))",
            "form=hiCreateLayoutForm(quote(cdlIncludeProbe) "
            '"CDL Include Probe" layout)',
            "hiInstantiateForm(form)",
            "defaultsOk=and(form~>cdlIncludeBtn~>value "
            'form~>cdlIncludeFile~>value=="/etc/hosts" '
            "form~>cdlIncludeFile~>enabled)",
            "form~>cdlIncludeBtn~>value=nil",
            "cadCdlIncludeCB(form)",
            "disabledOk=!form~>cdlIncludeFile~>enabled",
            'form~>inpType~>value="CDL+GDS"',
            "cadCdlIncludeInputTypeCB(form)",
            "cdlGdsHidden=form~>cdlInclude~>invisible",
            'form~>inpType~>value="CDL+LAY"',
            "cadCdlIncludeInputTypeCB(form)",
            "cdlLayHidden=form~>cdlInclude~>invisible",
            'form~>inpType~>value="SVDB"',
            "cadCdlIncludeInputTypeCB(form)",
            "svdbHidden=form~>cdlInclude~>invisible",
            'form~>inpType~>value="CCI"',
            "cadCdlIncludeInputTypeCB(form)",
            "cciHidden=form~>cdlInclude~>invisible",
            'form~>inpType~>value="OA"',
            "cadCdlIncludeInputTypeCB(form)",
            "oaVisible=!form~>cdlInclude~>invisible",
            'form~>inpType~>value="SCH+GDS"',
            "cadCdlIncludeInputTypeCB(form)",
            "schGdsVisible=!form~>cdlInclude~>invisible",
            "if(and(defaultsOk disabledOk cdlGdsHidden cdlLayHidden "
            "svdbHidden cciHidden oaVisible schGdsVisible) "
            'then printf("CAD_CDL_INCLUDE_FORM_OK\\n"))',
            "exit()",
        )
    )
    env = os.environ.copy()
    env["CDL_HEADER_FILE"] = "/etc/hosts"
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log:
        completed = subprocess.run(
            [
                "/bin/bash",
                "-c",
                'printf "%s\\n" "$1" | "$2" -nograph',
                "cdl-include-virtuoso-probe",
                skill,
                virtuoso,
            ],
            text=True,
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=CAD_ROOT,
            env=env,
            timeout=60,
            check=False,
        )
        log.seek(0)
        output = log.read()
    assert completed.returncode == 0, output
    assert "CAD_CDL_INCLUDE_FORM_OK" in output


@pytest.mark.skipif(
    os.environ.get("RCE_RUN_SKILL_PROBE") != "1",
    reason="set RCE_RUN_SKILL_PROBE=1 to run the Cadence Virtuoso probe",
)
def test_export_cdl_form_writes_selected_and_disabled_include(
    tmp_path: Path,
) -> None:
    virtuoso = _virtuoso()
    install = tmp_path / "install"
    install.mkdir()
    (install / "tools").symlink_to(CAD_ROOT, target_is_directory=True)
    include = tmp_path / "relative-header.cdl"
    include.write_text("* relative CDL include probe\n", encoding="utf-8")
    replay = tmp_path / "export_cdl_include.il"
    log = tmp_path / "export_cdl_include.log"
    replay.write_text(
        "\n".join(
            (
                f'setShellEnvVar("CAD_HOME" "{install}")',
                'setShellEnvVar("CDL_HEADER_FILE" "relative-header.cdl")',
                f'load("{install}/tools/lvs/skill++/LVS.ils")',
                "cadDisplayExportCdlForm()",
                "form=exportCdlForm",
                'form~>schLib~>value="probe"',
                'form~>schCell~>value="selected"',
                'form~>schView~>value="schematic"',
                f'form~>outDirPath~>value="{tmp_path}"',
                "selectedConfig=lvsStageWriteToml(form \"cdl\")",
                "form~>cdlIncludeBtn~>value=nil",
                "cadCdlIncludeCB(form)",
                'form~>schCell~>value="disabled"',
                "disabledConfig=lvsStageWriteToml(form \"cdl\")",
                "if(and(selectedConfig disabledConfig) "
                'then printf("EXPORT_CDL_INCLUDE_TOML_OK\\n"))',
                "when(form hiFormDone(form) hiDeleteForm(form))",
                "exit()",
                "",
            )
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["CDL_DB_DIR"] = str(tmp_path)
    completed = subprocess.run(
        [virtuoso, "-nograph", "-nocdsinit", "-replay", str(replay), "-log", str(log)],
        text=True,
        capture_output=True,
        cwd=tmp_path,
        env=env,
        timeout=80,
        check=False,
    )
    output = completed.stdout + completed.stderr
    if log.is_file():
        output += log.read_text(encoding="utf-8", errors="replace")
    assert completed.returncode == 0, output
    assert "*Error*" not in output
    assert "EXPORT_CDL_INCLUDE_TOML_OK" in output
    selected = (tmp_path / "probe.selected/export_cdl.toml").read_text(
        encoding="utf-8"
    )
    disabled = (tmp_path / "probe.disabled/export_cdl.toml").read_text(
        encoding="utf-8"
    )
    assert f'cdl_header_file = "{include}"' in selected
    assert 'cdl_header_file = ""' in disabled
