from __future__ import annotations

import os
from pathlib import Path

import pytest
from skill_probe_support import run_virtuoso_source


CAD_ROOT = Path(__file__).resolve().parents[3]
RUN_PROBE = os.environ.get("RCE_RUN_SKILL_PROBE") == "1"


def _run_virtuoso_probe(
    tmp_path: Path,
    replay_lines: tuple[str, ...],
    *,
    environment: dict[str, str],
) -> str:
    return run_virtuoso_source(
        "\n".join((*replay_lines, "exit()", "")), tmp_path,
        env_updates=environment, log_path=tmp_path / "virtuoso.log",
    )


def _fake_selector(path: Path, *, delay: bool = False) -> None:
    script = [
        "#!/bin/sh",
        'if [ "${1-}" = "rule-select-gui" ]; then shift; fi',
        "initial=",
        "output=",
        "while [ $# -gt 0 ]; do",
        '  case "$1" in',
        "    --initial) initial=$2; shift 2 ;;",
        "    --output) output=$2; shift 2 ;;",
        "    --parent-pid) shift 2 ;;",
        "    *) shift ;;",
        "  esac",
        "done",
    ]
    if delay:
        script.append("sleep 2")
    script.extend(
        (
            'test -z "${QT_XCB_NO_XI2-}" || exit 4',
            'test -z "${QT_XCB_NO_XI2_MOUSE-}" || exit 5',
            'test -f "$initial" || exit 3',
            "printf '#status\\tapplied\\nGROUP\\tGAA\\nCHECK\\tAA_2\\n' > \"$output\"",
            "exit 0",
            "",
        )
    )
    path.write_text("\n".join(script), encoding="utf-8")
    path.chmod(0o755)


@pytest.mark.skipif(not RUN_PROBE, reason="set RCE_RUN_SKILL_PROBE=1")
def test_pyqt_rule_select_apply_updates_main_form(tmp_path: Path) -> None:
    install = tmp_path / "install"
    install.mkdir()
    (install / "tools").symlink_to(CAD_ROOT, target_is_directory=True)
    rule_file = tmp_path / "rules.drc"
    rule_file.write_text("GROUP GAA AA_?\n", encoding="utf-8")
    fake = tmp_path / "fake-rule-select"
    _fake_selector(fake)
    environment = {}
    environment.update(
        {
            "DRC_DB_DIR": str(tmp_path),
            "DRC_FILE": f"probe,{rule_file}",
            "DRC_RULE_SELECT_PYTHON": str(fake),
            "QT_XCB_NO_XI2": "1",
            "QT_XCB_NO_XI2_MOUSE": "1",
        }
    )
    output = _run_virtuoso_probe(
        tmp_path,
        (
            f'setShellEnvVar("CAD_HOME" "{install}")',
            f'load("{install}/tools/drc/skill++/DRC.ils")',
            "cadDisplayDrcForm()",
            "form=drcForm",
            "form~>drcRuleSelectEnable~>value=t",
            "drcRuleSelectEnableCB(form)",
            f'form~>drcRunsetFile~>value="{rule_file}"',
            "drcRuleSelectOpenCB(form)",
            "cid=form~>drcRuleSelectCid",
            "startedOk=and(cid !form~>drcRuleSelectOpen~>enabled)",
            "openSummaryOk=and(form~>drcRuleSelectSummary~>value==",
            '  "Rule Select window is open..."',
            "  form~>drcRuleSelectSummary~>_labelOnly==",
            '  "Rule Select window is open...")',
            "ipcWait(cid 1 10)",
            'appliedOk=and(equal(form~>drcRuleSelectGroups list("GAA")) ',
            '  equal(form~>drcRuleSelectChecks list("AA_2")) ',
            f'  form~>drcRuleSelectRuleFile=="{rule_file}")',
            "summaryOk=and(form~>drcRuleSelectSummary~>value==",
            '  "1 groups, 1 checks selected"',
            "  form~>drcRuleSelectSummary~>_labelOnly==",
            '  "1 groups, 1 checks selected")',
            "when(and(startedOk openSummaryOk appliedOk summaryOk)",
            '  printf("DRC_RULE_SELECT_PYQT_APPLY_OK\\n"))',
            "drcRuleSelectFormDone(form)",
        ),
        environment=environment,
    )
    assert "\\o DRC_RULE_SELECT_PYQT_APPLY_OK" in output


@pytest.mark.skipif(not RUN_PROBE, reason="set RCE_RUN_SKILL_PROBE=1")
def test_pyqt_rule_select_rejects_result_after_runset_change(tmp_path: Path) -> None:
    install = tmp_path / "install"
    install.mkdir()
    (install / "tools").symlink_to(CAD_ROOT, target_is_directory=True)
    first_rule = tmp_path / "first.drc"
    second_rule = tmp_path / "second.drc"
    first_rule.write_text("GROUP FIRST first_?\n", encoding="utf-8")
    second_rule.write_text("GROUP SECOND second_?\n", encoding="utf-8")
    fake = tmp_path / "fake-rule-select"
    _fake_selector(fake, delay=True)
    environment = {}
    environment.update(
        {
            "DRC_DB_DIR": str(tmp_path),
            "DRC_FILE": f"probe,{first_rule}",
            "DRC_RULE_SELECT_PYTHON": str(fake),
        }
    )
    output = _run_virtuoso_probe(
        tmp_path,
        (
            f'setShellEnvVar("CAD_HOME" "{install}")',
            f'load("{install}/tools/drc/skill++/DRC.ils")',
            "cadDisplayDrcForm()",
            "form=drcForm",
            "form~>drcRuleSelectEnable~>value=t",
            "drcRuleSelectEnableCB(form)",
            f'form~>drcRunsetFile~>value="{first_rule}"',
            "drcRuleSelectOpenCB(form)",
            "cid=form~>drcRuleSelectCid",
            f'form~>drcRunsetFile~>value="{second_rule}"',
            "drcRuleSelectInvalidate(form)",
            "cancelledOk=!form~>drcRuleSelectCid",
            "ipcWait(cid 1 10)",
            "staleOk=and(cancelledOk !form~>drcRuleSelectGroups",
            '  !form~>drcRuleSelectChecks form~>drcRuleSelectRuleFile=="")',
            "summaryOk=and(form~>drcRuleSelectSummary~>value==",
            '  "No rules selected"',
            "  form~>drcRuleSelectSummary~>_labelOnly==",
            '  "No rules selected")',
            'when(and(staleOk summaryOk) printf("DRC_RULE_SELECT_PYQT_STALE_OK\\n"))',
            "drcRuleSelectFormDone(form)",
        ),
        environment=environment,
    )
    assert "\\o DRC_RULE_SELECT_PYQT_STALE_OK" in output


def test_pyqt_rule_select_skill_protocol_is_guarded_and_tree_free() -> None:
    selector = (CAD_ROOT / "drc/skill++/DRCRULESEL.ils").read_text(encoding="utf-8")
    loader = (CAD_ROOT / "drc/skill++/DRC.ils").read_text(encoding="utf-8")
    protocol = (CAD_ROOT / "common/skill/SICO_guiProtocol.il").read_text(
        encoding="utf-8"
    )

    assert '" rule-select-gui "' in selector
    assert 'info("<INFO> DRC Rule Select window started.\\n")' in selector
    assert "DRC Rule Select PyQt5 window started." not in selector
    assert "drcRuleSelectWriteInitial" in selector
    assert "form~>drcRuleSelectSummary~>value=text" in selector
    assert "form~>drcRuleSelectSummary~>_labelOnly=text" in selector
    assert 'SICO_guiProtocolRead(path list("GROUP" "CHECK") "applied")' in selector
    assert "case(kind" in protocol
    assert '("#status"' in protocol
    assert "form~>drcRuleSelectRequestToken==state['token]" in selector
    assert "currentRuleFile==state['ruleFile]" in selector
    assert "hiIsFormDisplayed(form)" in selector
    assert 'SICO_guiPythonLaunchCommand(args "DRC_ORIG_LD_LIBRARY_PATH")' in selector
    assert "exec env -u PYTHONHOME -u PYTHONPATH" in protocol
    assert "-u QT_QPA_PLATFORM_PLUGIN_PATH" in protocol
    assert "-u QT_XCB_NO_XI2 -u QT_XCB_NO_XI2_MOUSE" in protocol
    assert 'strcat(drcCommonRoot "/skill/SICO_guiProtocol.il")' in loader
    assert "drcRuleSelectRegisterExitCleanup" in selector
    assert "DRCRULESELTREE.ils" not in loader
    assert not (CAD_ROOT / "drc/skill++/DRCRULESELTREE.ils").exists()
