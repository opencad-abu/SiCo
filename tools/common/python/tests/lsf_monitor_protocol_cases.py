from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from skill_test_support import common_source_loads, skill_group_paths

CAD_ROOT = Path(__file__).resolve().parents[3]
RUN_PROBE = os.environ.get("RCE_RUN_SKILL_PROBE") == "1"


@pytest.mark.skipif(
    not RUN_PROBE,
    reason="set RCE_RUN_SKILL_PROBE=1 to run the Cadence dbAccess probe",
)
def test_lsf_monitor_selector_protocol_with_dbaccess(tmp_path: Path) -> None:
    dbaccess = shutil.which(os.environ.get("RCE_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")
    install = tmp_path / "install"
    install.mkdir()
    (install / "tools").symlink_to(CAD_ROOT, target_is_directory=True)
    sources = (
        CAD_ROOT / "common/skill/SICO_toml.il",
        CAD_ROOT / "common/skill/SICO_guiProtocol.il",
        *skill_group_paths("common", "lsf"),
        CAD_ROOT / "common/skill/SICO_lsfMonitor.il",
    )
    applied = tmp_path / "applied.tsv"
    duplicate = tmp_path / "duplicate.tsv"
    invalid = tmp_path / "invalid.tsv"
    stale = tmp_path / "stale.tsv"
    skill = "\n".join(
        (
            f'setShellEnvVar("CAD_HOME" "{install}")',
            *(f'load("{source}")' for source in sources),
            "defstruct(mockMonitorRunType value)",
            "defstruct(mockMonitorForm runType queueName srvName)",
            "procedure(hiIsForm(form) t)",
            "procedure(SICO_lsfStartQueueDiscovery(form) "
            "mockDiscoveryCount=add1(mockDiscoveryCount) t)",
            "mockDiscoveryCount=0",
            'runType=make_mockMonitorRunType(?value "LSF Farm")',
            "form=make_mockMonitorForm(?runType runType)",
            'command=SICO_lsfMonitorLaunchCommand("normal" "/tmp/result.tsv")',
            f'SICO_guiProtocolWrite("{applied}" '
            'list(list("QUEUE" "normal") list("HOST" "node01")) '
            '"applied" "1")',
            f'appliedResult=SICO_lsfMonitorReadSelection("{applied}")',
            "state=makeTable(\"monitorApplied\" nil)",
            "state['mode]='selector",
            "state['form]=form",
            "state['cid]=101",
            "state['token]=1",
            f'state[\'resultFile]="{applied}"',
            "state['cancelled]=nil",
            "cadLsfMonitorProcesses[101]=state",
            "putpropq(form 101 cadLsfMonitorCid)",
            "putpropq(form 1 cadLsfMonitorRequestToken)",
            "SICO_lsfMonitorPostFunc(101 0)",
            "appliedOk=and(mockDiscoveryCount==1 "
            'get(form \'cadLsfPreferredQueue)=="normal" '
            'get(form \'cadLsfPreferredHost)=="node01")',
            f'SICO_guiProtocolWrite("{duplicate}" '
            'list(list("QUEUE" "normal") list("QUEUE" "batch")) '
            '"applied" "1")',
            f'duplicateResult=SICO_lsfMonitorReadSelection("{duplicate}")',
            f'SICO_guiProtocolWrite("{invalid}" '
            'list(list("QUEUE" "normal")) "applied" "2")',
            f'invalidResult=SICO_lsfMonitorReadSelection("{invalid}")',
            f'SICO_guiProtocolWrite("{stale}" '
            'list(list("QUEUE" "batch")) "applied" "1")',
            "staleState=makeTable(\"monitorStale\" nil)",
            "staleState['mode]='selector",
            "staleState['form]=form",
            "staleState['cid]=102",
            "staleState['token]=1",
            f'staleState[\'resultFile]="{stale}"',
            "staleState['cancelled]=nil",
            "cadLsfMonitorProcesses[102]=staleState",
            "putpropq(form 102 cadLsfMonitorCid)",
            "putpropq(form 2 cadLsfMonitorRequestToken)",
            "SICO_lsfMonitorPostFunc(102 0)",
            "staleOk=mockDiscoveryCount==1",
            'when(and(command rexMatchp("sico-lsf.*monitor" command) '
            'rexMatchp("--output.*result.tsv" command) car(appliedResult) '
            '!car(duplicateResult) !car(invalidResult) appliedOk staleOk) '
            'printf("CAD_LSF_MONITOR_SELECTOR_OK\\n"))',
            'printf("CAD_LSF_MONITOR_REV=%s\\n" SICO_lsfMonitorRevision())',
            "exit()",
        )
    )
    completed = subprocess.run(
        [dbaccess],
        input=skill,
        text=True,
        capture_output=True,
        timeout=40,
        check=False,
        cwd=tmp_path,
    )
    output = completed.stdout + completed.stderr

    assert completed.returncode == 0, output
    assert "*Error*" not in output
    assert "(reader)" not in output
    assert "still unclosed on EOF" not in output
    assert "CAD_LSF_MONITOR_REV=20260924.sico.monitor.environment.v3" in output
    assert "CAD_LSF_MONITOR_SELECTOR_OK" in output



@pytest.mark.skipif(
    not RUN_PROBE,
    reason="set RCE_RUN_SKILL_PROBE=1 to run the Cadence dbAccess probe",
)
def test_lsf_monitor_button_callback_with_dbaccess() -> None:
    dbaccess = shutil.which(os.environ.get("RCE_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")
    skill = "\n".join(
        (
            common_source_loads("gui"),
            "mockSelectorCalls=0",
            "mockSelectorForm='mockForm",
            "procedure(SICO_lsfMonitorStartSelector(form) "
            "mockSelectorCalls=add1(mockSelectorCalls) "
            "mockSelectorArg=form t)",
            "procedure(hiGetCurrentForm() mockSelectorForm)",
            'evalstring("cadLsfMonitorSelectorCB(hiGetCurrentForm())")',
            "when(and(mockSelectorCalls==1 "
            "mockSelectorArg==mockSelectorForm) "
            'printf("CAD_LSF_MONITOR_BUTTON_CALLBACK_OK\\n"))',
            "exit()",
        )
    )
    completed = subprocess.run(
        [dbaccess],
        input=skill,
        text=True,
        capture_output=True,
        timeout=40,
        check=False,
    )
    output = completed.stdout + completed.stderr

    assert completed.returncode == 0, output
    assert "*Error*" not in output
    assert "CAD_LSF_MONITOR_BUTTON_CALLBACK_OK" in output
