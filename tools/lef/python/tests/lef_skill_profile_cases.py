from __future__ import annotations

import os
import shutil
import subprocess

import pytest
from skill_integration_fixtures import CAD_ROOT


def test_lef_profile_adapter_covers_business_fields_transactionally() -> None:
    profile = (CAD_ROOT / "lef/skill++/LEFPROFILE.ils").read_text(
        encoding="utf-8"
    )
    frontend = (CAD_ROOT / "lef/skill++/LEF.ils").read_text(encoding="utf-8")
    gui = (CAD_ROOT / "lef/skill++/LEFGUI.ils").read_text(encoding="utf-8")

    for path in (
        "run.root_type",
        "run.root",
        "run.cds_lib",
        "run.run_type",
        "run.queue_name",
        "run.server_name",
        "run.cpus",
        "run.executable",
        "input.library",
        "input.cell_list_file",
        "input.cells",
        "input.cell",
        "input.layout_view",
        "input.logical_view",
        "input.abstract_view",
        "abstract.options_file",
        "abstract.bin",
        "abstract.bin_options.",
        "steps.pins",
        "steps.extract",
        "steps.abstract",
        "output.lef_file",
        "output.lef_version",
        "output.geometry",
        "output.technology",
    ):
        assert f'"{path}"' in profile
    collect = profile.split("procedure(lefProfileCollectForm", 1)[1].split(
        "procedure(lefProfileBinOptions", 1
    )[0]
    assert '"run.run_dir"' not in collect
    assert '"profile.path"' not in collect
    assert "lefProfileSnapshot(form)" in profile
    assert "errset(progn(" in profile
    assert "lefProfileRestore(form snapshot)" in profile
    assert 'SICO_profileRegisterAdapter("LEF"' in profile
    assert '"/skill/SICO_profile.il"' in frontend
    assert '"/skill++/PROFILEGUI.ils"' in frontend
    assert '"/skill++/LEFPROFILE.ils"' in frontend
    assert 'profileGui->profileFlow="LEF"' in gui



def test_lef_profile_collector_preserves_hidden_executable_state() -> None:
    profile = (CAD_ROOT / "lef/skill++/LEFPROFILE.ils").read_text(
        encoding="utf-8"
    )
    collect = profile.split("procedure(lefProfileCollectForm", 1)[1].split(
        "procedure(lefProfileBinOptions", 1
    )[0]

    assert "executable=lefLoadedAbstractExecutable()" in collect
    assert 'list("run.executable" executable)' in collect



def test_lef_profile_failed_apply_restores_hidden_state_with_dbaccess() -> None:
    dbaccess = shutil.which(os.environ.get("LEF_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")
    sources = (
        CAD_ROOT / "common/skill/SICO_toml.il",
        CAD_ROOT / "common/skill/SICO_profile.il",
        CAD_ROOT / "lef/skill++/LEFOPT.ils",
        CAD_ROOT / "lef/skill++/LEFLOAD.ils",
        CAD_ROOT / "lef/skill++/LEFPROFILE.ils",
    )
    skill = "\n".join(
            (
                "procedure(lefDefaultRunDir(root lib cell view batch) \"run\")",
                "procedure(lefDefaultOutputPath(root lib cell view batch) \"out.lef\")",
                "procedure(SICO_lsfLocalHost() \"localhost\")",
                'procedure(SICO_lsfSelectedHost(host) '
                'if(host=="Auto (LSF Scheduler)" then "" else host))',
            "procedure(lefCellListValue(form) nil)",
            "procedure(lefInlineCellsValue() nil)",
            *(f'load("{source}")' for source in sources),
            "procedure(lefProfileApplyForm(form data) let((executable) "
            'executable=SICO_profileDataValue(data "run.executable" nil) '
            "lefSetLoadedExecutable(executable) "
            'or(SICO_profileDataValue(data "accept" nil) '
            'executable=="/opt/cad/abstract")))',
            "SICO_profileRegisterAdapter(\"LEF\" "
            "'lefProfileCollectForm 'lefProfileApplyForm)",
            'lefSetLoadedExecutable("/opt/cad/abstract")',
            "form=makeTable(\"lefProfileRollbackForm\" nil)",
            "foreach(fieldName lefProfileFieldNames() "
            "field=makeTable(sprintf(nil \"lefRollbackField.%L\" fieldName) nil) "
            'field[\'value]="" field[\'enabled]=t field[\'invisible]=nil '
            'field[\'items]=list("") form[fieldName]=field)',
            'form[\'outDirType][\'value]="Customize Directory"',
            'form[\'outDirPath][\'value]="/runs"',
            'form[\'cdsLib][\'value]="/work/cds.lib"',
            'form[\'runType][\'value]="Current Host"',
            'form[\'runCpu][\'value]="1"',
            'form[\'lefLib][\'value]="demo"',
            'form[\'lefCell][\'value]="INVX1"',
            'form[\'layoutView][\'value]="layout"',
            'form[\'logicalView][\'value]="schematic"',
            'form[\'abstractView][\'value]="abstract"',
            'form[\'binName][\'value]="Core"',
            'form[\'runPins][\'value]=t',
            'form[\'runExtract][\'value]=t',
            'form[\'runAbstract][\'value]=t',
            'form[\'lefVersion][\'value]="5.8"',
            'form[\'exportGeometry][\'value]=t',
            "when(and(!SICO_profileApply(form \"LEF\" "
            'list(list("run.executable" "/bad/abstract") '
            'list("accept" nil))) '
            'lefLoadedAbstractExecutable()=="/opt/cad/abstract")',
            '  printf("LEF_PROFILE_HIDDEN_ROLLBACK_OK\\n"))',
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
    assert "LEF_PROFILE_HIDDEN_ROLLBACK_OK" in output
