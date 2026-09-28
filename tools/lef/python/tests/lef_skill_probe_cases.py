from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from skill_integration_fixtures import CAD_ROOT, _balanced


def test_lef_common_hardware_fields_with_dbaccess(tmp_path: Path) -> None:
    dbaccess = shutil.which(os.environ.get("LEF_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")
    install = tmp_path / "install"
    install.mkdir()
    (install / "tools").symlink_to(CAD_ROOT, target_is_directory=True)
    entry = CAD_ROOT / "lef/skill++/LEF.ils"
    skill = "\n".join(
        (
            f'setShellEnvVar("CAD_HOME" "{install}")',
            f'load("{entry}")',
            "defstruct(lefHardwareProbeField name value items enabled callback)",
            "procedure(hiCreateComboField(@key name prompt defValue items "
            "editable enabled callback) make_lefHardwareProbeField("
            "?name name ?value defValue ?items items ?enabled enabled "
            "?callback callback))",
            "procedure(hiCreateButton(@key name buttonText buttonIcon callback "
            "enabled toolTip) make_lefHardwareProbeField("
            "?name name ?enabled enabled ?callback callback))",
            "procedure(hiCreateHorizontalBoxLayout(name @key frame horiz_align "
            "spacing items invisible) items)",
            "procedure(SICO_lsfMonitorButtonIcon() 'monitorIcon)",
            'procedure(pwd() "/tmp")',
            "gui=makeInstance('LEFPROFILEGUI)",
            'gui->lsfChoicesCB="(SICO_lsfChoicesCB lefForm)"',
            'gui->queueCB="(SICO_lsfQueueChoicesCB lefForm)"',
            "makeServers(gui)",
            "makeRunCpus(gui)",
            "when(and(gui->runType gui->queueName gui->srvName "
            "gui->lsfMonitor gui->runCpu gui->servers "
            "gui->lsfMonitor->name=='lsfMonitor "
            "gui->lsfMonitor->callback=="
            '"cadLsfMonitorSelectorCB(hiGetCurrentForm())") '
            'printf("LEF_COMMON_HARDWARE_OK\\n"))',
            "exit()",
        )
    )
    completed = subprocess.run(
        [dbaccess],
        input=skill,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "*Error*" not in output
    assert "(reader)" not in output
    assert "LEF_COMMON_HARDWARE_OK" in output



@pytest.mark.skipif(
    os.environ.get("LEF_RUN_SKILL_PROBE") != "1",
    reason="set LEF_RUN_SKILL_PROBE=1 to run the Cadence dbAccess probe",
)
def test_lef_frontend_dbaccess_probe(tmp_path: Path) -> None:
    dbaccess = shutil.which(os.environ.get("LEF_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")
    install = tmp_path / "install"
    install.mkdir()
    (install / "tools").symlink_to(CAD_ROOT, target_is_directory=True)
    cds_lib = tmp_path / "cds.lib"
    options_file = tmp_path / "abstract.options"
    config_file = tmp_path / "lef.toml"
    cds_lib.write_text("DEFINE demo ./demo\n", encoding="utf-8")
    options_file.write_text("; options\n", encoding="utf-8")
    config_file.write_text(
        f"""
[cad_config]
format = "sico-flow-profile"
version = 1
flow = "LEF"

[run]
root_type = "Customize Directory"
root = "{tmp_path / 'run'}"
cds_lib = "{cds_lib}"

[input]
library = "demo"
cells = ["INVX1", "NAND2X1"]

[abstract]
options_file = "{options_file}"

[abstract.bin_options]
ExtractSig = true
CustomOption = "preserved"
NumericOption = 7

[output]
lef_file = "demo.lef"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    shared_profile = (CAD_ROOT / "common/skill/SICO_profile.il").read_text(
        encoding="utf-8"
    )
    if not _balanced(shared_profile):
        pytest.skip("shared SICO_profile.il is being updated by the common-profile task")
    test_env = os.environ.copy()
    python_executable = Path("/software/pkgs/python/3.9.13/bin/python3")
    if not python_executable.is_file():
        python_executable = Path(sys.executable)
    if subprocess.run(
        [str(python_executable), "-s", "-c", "import tomli"],
        capture_output=True,
        check=False,
    ).returncode:
        pytest.skip("profile probe Python lacks tomli")
    test_env["CAD_PYTHON"] = str(python_executable)
    skill = "\n".join(
        (
            f'setShellEnvVar("CAD_HOME" "{install}")',
            f'load("{install}/tools/lef/skill++/LEF.ils")',
            "if(and(isCallable('cadDisplayLefForm) isCallable('lefWriteToml) "
            "isCallable('lefStart) isCallable('lefRunTagFromForm) "
            "isCallable('LEF_pythonEntry) "
            "isCallable('LEF_pythonExe)) "
            'then printf("LEF_FRONTEND_LOAD_OK\\n"))',
            'if(lefDefaultOutputPath("/tmp/run" "demo" "INVX1" "layout" nil)=='
            '"/tmp/run/demo.INVX1.layout/INVX1.lef" '
            'then printf("LEF_SINGLE_PATH_OK\\n"))',
            'if(lefDefaultOutputPath("/tmp/run" "demo" "" "layout" "/tmp/core.cells")=='
            '"/tmp/run/demo.batch.layout/demo.lef" '
            'then printf("LEF_BATCH_PATH_OK\\n"))',
            'if(lefOptionSpecCount()==29 '
            'then printf("LEF_OPTION_SPECS_OK\\n"))',
            'if(lefOptionSpecByName("ExtractSig") '
            'then printf("LEF_OPTION_LOOKUP_OK\\n"))',
            'if(lefConfig()->defaultBin=="Core" '
            'then printf("LEF_SHARED_CONFIG_OK\\n"))',
            'fakeLoadForm=makeTable("lefFakeLoadForm" nil)',
            "foreach(fieldName cons('profileFile lefProfileFieldNames()) "
            "field=makeTable(sprintf(nil \"lefField.%L\" fieldName) nil) "
            "field['value]=\"\" "
            "field['enabled]=t field['invisible]=nil field['items]=list(\"\") "
            "fakeLoadForm[fieldName]=field)",
            f'fakeLoadForm[\'profileFile][\'value]="{config_file}"',
            'if(lefLoadConfigCB(fakeLoadForm) '
            'then printf("LEF_LOAD_CALLBACK_OK\\n"))',
            f'if(lefRunDirFromForm(fakeLoadForm)=="{tmp_path / "run" / "demo.batch.layout"}" '
            'then printf("LEF_DERIVED_RUN_DIR_OK\\n"))',
            f'if(and(!fakeLoadForm[\'runDir] '
            f'fakeLoadForm[\'lefFile][\'value]=="{tmp_path / "demo.lef"}") '
            'then printf("LEF_NO_VISIBLE_RUN_DIR_OK\\n"))',
            'if(fakeLoadForm[\'inlineCells][\'invisible]==nil '
            'then printf("LEF_LOAD_INLINE_CELLS_OK\\n"))',
            'if(fakeLoadForm[\'runExtract][\'value] '
            'then printf("LEF_GENERATED_BOOL_OK\\n"))',
            'if(length(lefLoadedInlineCellNames())==2 '
            'then printf("LEF_GENERATED_CELLS_OK\\n"))',
            'if(lefFormBinOptions(fakeLoadForm)==list('
            'list("CustomOption" "preserved") list("NumericOption" "7") '
            'list("ExtractSig" "true")) '
            'then printf("LEF_BIN_OPTIONS_ROUNDTRIP_OK\\n"))',
            'setShellEnvVar("CAD_PYTHON" "/cad/python")',
            'if(LEF_pythonExe()=="/cad/python" '
            'then printf("LEF_CAD_PYTHON_INHERIT_OK\\n"))',
            'setShellEnvVar("LEF_PYTHON" "/lef/python")',
            'if(LEF_pythonExe()=="/lef/python" '
            'then printf("LEF_PYTHON_OVERRIDE_OK\\n"))',
            "exit()",
        )
    )
    completed = subprocess.run(
        [dbaccess],
        input=skill,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
        env=test_env,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "*Error*" not in output
    assert "LEF_FRONTEND_LOAD_OK" in output
    assert "LEF_SINGLE_PATH_OK" in output
    assert "LEF_BATCH_PATH_OK" in output
    assert "LEF_OPTION_SPECS_OK" in output
    assert "LEF_OPTION_LOOKUP_OK" in output
    assert "LEF_SHARED_CONFIG_OK" in output
    assert "LEF_GENERATED_BOOL_OK" in output
    assert "LEF_GENERATED_CELLS_OK" in output
    assert "LEF_LOAD_CALLBACK_OK" in output
    assert "LEF_DERIVED_RUN_DIR_OK" in output
    assert "LEF_NO_VISIBLE_RUN_DIR_OK" in output
    assert "LEF_LOAD_INLINE_CELLS_OK" in output
    assert "LEF_BIN_OPTIONS_ROUNDTRIP_OK" in output
    assert "LEF_CAD_PYTHON_INHERIT_OK" in output
    assert "LEF_PYTHON_OVERRIDE_OK" in output
