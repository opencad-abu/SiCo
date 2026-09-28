from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


CAD_ROOT = Path(__file__).resolve().parents[3]
RUN_PROBE = os.environ.get("RCE_RUN_SKILL_PROBE") == "1"


def _assert_clean(output: str) -> None:
    assert "*Error*" not in output
    assert "(reader)" not in output
    assert "still unclosed on EOF" not in output


def _install_tree(tmp_path: Path) -> Path:
    install = tmp_path / "install"
    install.mkdir()
    (install / "tools").symlink_to(CAD_ROOT, target_is_directory=True)
    return install


def _probe_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    for name in ("DRC_DB_DIR", "LVS_DB_DIR", "RCE_DB_DIR"):
        env[name] = str(tmp_path)
    return env


@pytest.mark.skipif(not RUN_PROBE, reason="set RCE_RUN_SKILL_PROBE=1")
@pytest.mark.parametrize(
    ("flow", "entry", "display", "form_name", "paired"),
    (
        ("DRC", "drc/skill++/DRC.ils", "cadDisplayDrcForm", "drcForm", False),
        ("LVS", "lvs/skill++/LVS.ils", "cadDisplayLvsForm", "lvsForm", True),
        ("RCE", "rce/skill++/RCE.ils", "cadDisplayRceForm", "rceForm", True),
    ),
)
def test_batch_forms_instantiate_with_virtuoso(
    tmp_path: Path,
    flow: str,
    entry: str,
    display: str,
    form_name: str,
    paired: bool,
) -> None:
    virtuoso = shutil.which(os.environ.get("RCE_VIRTUOSO", "virtuoso"))
    if virtuoso is None:
        pytest.skip("virtuoso is unavailable")
    install = _install_tree(tmp_path)
    probe = tmp_path / f"{flow.lower()}_batch_form.il"
    log = tmp_path / f"{flow.lower()}_virtuoso.log"
    flow_check = (
        "probeForm~>inpType~>value==\"OA\" !probeForm~>inpType~>enabled "
        "probeForm~>designInp~>invisible"
        if paired
        else "probeForm~>layInp~>invisible"
    )
    custom_svrf_checks = (
        "svrfInitialOk=and(probeForm~>customSvrfEnable "
        "probeForm~>customSvrfCommand "
        "!probeForm~>customSvrfEnable~>value "
        "probeForm~>customSvrfCommand~>value==\"\" "
        "probeForm~>customSvrfCommand~>invisible)",
        "probeForm~>customSvrfEnable~>value=t",
        "cadCustomSvrfCB(probeForm)",
        "svrfShownOk=!probeForm~>customSvrfCommand~>invisible",
        "probeForm~>customSvrfEnable~>value=nil",
        "cadCustomSvrfCB(probeForm)",
        "svrfHiddenOk=probeForm~>customSvrfCommand~>invisible",
    )
    rule_select_checks = (
        (
            "ruleSelectInitialOk=and(probeForm~>drcRuleSelectEnable "
            "probeForm~>drcRuleSelectOpen probeForm~>drcRuleSelectSummary "
            "!probeForm~>drcRuleSelectEnable~>value "
            "!probeForm~>drcRuleSelectOpen~>enabled "
            "probeForm~>drcRuleSelectGroups==nil "
            "probeForm~>drcRuleSelectChecks==nil)",
            "probeForm~>drcRuleSelectEnable~>value=t",
            "drcRuleSelectEnableCB(probeForm)",
            "ruleSelectEnabledOk=probeForm~>drcRuleSelectOpen~>enabled",
        )
        if flow == "DRC"
        else (
            "ruleSelectInitialOk=t",
            "ruleSelectEnabledOk=t",
        )
    )
    input_callback = "rceInpCB" if flow == "RCE" else "lvsInpCB"
    rule_file_check = (
        'probeForm~>drcRunsetName~>prompt=="Rule File:"'
        if flow == "DRC"
        else 'probeForm~>lvsRunsetName~>prompt=="Rule File:"'
    )
    cdl_include_checks = (
        (
            "cdlIncludeInitialOk=and(probeForm~>cdlIncludeBtn "
            "probeForm~>cdlIncludeFile probeForm~>cdlInclude "
            "!probeForm~>cdlInclude~>invisible)",
            'probeForm~>batchRunScope~>value="Single Cell"',
            "cadBatchScopeCB(probeForm)",
            'probeForm~>inpType~>value="CDL+GDS"',
            f"{input_callback}(probeForm)",
            "cdlIncludeCdlGdsHidden=probeForm~>cdlInclude~>invisible",
            'probeForm~>inpType~>value="CDL+LAY"',
            f"{input_callback}(probeForm)",
            "cdlIncludeCdlLayHidden=probeForm~>cdlInclude~>invisible",
            'probeForm~>inpType~>value="SCH+GDS"',
            f"{input_callback}(probeForm)",
            "cdlIncludeSchGdsVisible=!probeForm~>cdlInclude~>invisible",
            'probeForm~>inpType~>value="OA"',
            f"{input_callback}(probeForm)",
            "cdlIncludeOaVisible=!probeForm~>cdlInclude~>invisible",
            'probeForm~>inpType~>value="CDL+GDS"',
            f"{input_callback}(probeForm)",
            'probeForm~>batchRunScope~>value="Multiple Cells"',
            "cadBatchScopeCB(probeForm)",
            "cdlIncludeBatchVisible=and(probeForm~>inpType~>value==\"OA\" "
            "!probeForm~>cdlInclude~>invisible)",
            'probeForm~>batchRunScope~>value="Single Cell"',
            "cadBatchScopeCB(probeForm)",
            "cdlIncludeSingleHidden=and(probeForm~>inpType~>value==\"CDL+GDS\" "
            "probeForm~>cdlInclude~>invisible)",
        )
        if flow in {"LVS", "RCE"}
        else (
            "cdlIncludeInitialOk=t",
            "cdlIncludeCdlGdsHidden=t",
            "cdlIncludeCdlLayHidden=t",
            "cdlIncludeSchGdsVisible=t",
            "cdlIncludeOaVisible=t",
            "cdlIncludeBatchVisible=t",
            "cdlIncludeSingleHidden=t",
        )
    )
    advanced_options_checks = (
        (
            "advancedInitialOk=and(probeForm~>inputMore "
            "probeForm~>inputMoreContents probeForm~>lvsMore "
            "probeForm~>lvsMoreContents probeForm~>moreOpt "
            "!probeForm~>inputMore~>value "
            "probeForm~>inputMoreContents~>invisible "
            "!probeForm~>lvsMore~>value "
            "probeForm~>lvsMoreContents~>invisible "
            "!probeForm~>moreOpt~>value "
            "probeForm~>extractTopCellRow~>invisible "
            "probeForm~>nameSource~>invisible)",
            "probeForm~>inputMore~>value=t",
            "probeForm~>lvsMore~>value=t",
            "probeForm~>moreOpt~>value=t",
            "advancedExpandedOk=and(!probeForm~>inputMoreContents~>invisible "
            "!probeForm~>lvsMoreContents~>invisible "
            "!probeForm~>extractTopCellRow~>invisible "
            "!probeForm~>nameSource~>invisible)",
        )
        if flow == "RCE"
        else (
            (
                "advancedInitialOk=and(probeForm~>drcMore "
                "probeForm~>drcMoreContents !probeForm~>drcMore~>value "
                "probeForm~>drcMoreContents~>invisible)",
                "probeForm~>drcMore~>value=t",
                "advancedExpandedOk=!probeForm~>drcMoreContents~>invisible",
            )
            if flow == "DRC"
            else (
                "advancedInitialOk=and(probeForm~>inputMore "
                "probeForm~>inputMoreContents probeForm~>lvsMore "
                "probeForm~>lvsMoreContents !probeForm~>inputMore~>value "
                "probeForm~>inputMoreContents~>invisible "
                "!probeForm~>lvsMore~>value "
                "probeForm~>lvsMoreContents~>invisible)",
                "probeForm~>inputMore~>value=t",
                "probeForm~>lvsMore~>value=t",
                "advancedExpandedOk=and(!probeForm~>inputMoreContents~>invisible "
                "!probeForm~>lvsMoreContents~>invisible)",
            )
        )
    )
    probe.write_text(
        "\n".join(
            (
                f'setShellEnvVar("CAD_HOME" "{install}")',
                f'load("{install}/tools/{entry}")',
                f"{display}()",
                f"probeForm={form_name}",
                "singleOk=and(probeForm probeForm~>batchRunScope "
                "probeForm~>batchTaskReport probeForm~>batchTaskLay~>invisible "
                "probeForm~>batchParallelCells~>invisible "
                f"{rule_file_check} "
                "probeForm~>batchTaskReport~>hiContextMenu "
                "probeForm~>batchTaskReport~>hiShowContextMenuCallback=="
                "'cadBatchTaskContextMenuCB "
                "!get(probeForm 'batchTaskRemove) "
                "!get(probeForm 'batchTaskClear))",
                'probeForm~>batchRunScope~>value="Multiple Cells"',
                "cadBatchScopeCB(probeForm)",
                "multipleOk=and(!probeForm~>batchTaskLay~>invisible "
                f"!probeForm~>batchParallelCells~>invisible {flow_check})",
                *custom_svrf_checks,
                *rule_select_checks,
                *cdl_include_checks,
                *advanced_options_checks,
                "if(and(singleOk multipleOk svrfInitialOk svrfShownOk "
                "svrfHiddenOk ruleSelectInitialOk ruleSelectEnabledOk "
                "cdlIncludeInitialOk cdlIncludeCdlGdsHidden "
                "cdlIncludeCdlLayHidden cdlIncludeSchGdsVisible "
                "cdlIncludeOaVisible cdlIncludeBatchVisible "
                "cdlIncludeSingleHidden advancedInitialOk "
                "advancedExpandedOk) "
                f'then printf("{flow}_BATCH_FORM_OK\\n"))',
                "when(probeForm hiFormDone(probeForm) hiDeleteForm(probeForm))",
                "exit()",
                "",
            )
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [virtuoso, "-nograph", "-nocdsinit", "-replay", str(probe), "-log", str(log)],
        text=True,
        capture_output=True,
        timeout=80,
        check=False,
        env=_probe_env(tmp_path),
    )
    output = completed.stdout + completed.stderr
    if log.is_file():
        output += log.read_text(encoding="utf-8", errors="replace")
    assert completed.returncode == 0, output
    _assert_clean(output)
    assert f"{flow}_BATCH_FORM_OK" in output
