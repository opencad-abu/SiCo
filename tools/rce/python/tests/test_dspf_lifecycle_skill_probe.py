from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


CAD_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.skipif(
    os.environ.get("RCE_RUN_SKILL_PROBE") != "1",
    reason="set RCE_RUN_SKILL_PROBE=1 to run the Cadence dbAccess probe",
)
def test_dspf_exit_cleanup_stops_bridge_and_all_standalone_processes() -> None:
    dbaccess = shutil.which(os.environ.get("RCE_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")
    helper = CAD_ROOT / "rce/skill/RCE_toml.il"
    gui_protocol = CAD_ROOT / "common/skill/SICO_guiProtocol.il"
    launcher = CAD_ROOT / "rce/skill/RCE_dspfLauncher.il"
    skill = "\n".join(
        (
            "mockAnalyzerStops=0",
            'mockParentData=""',
            "procedure(rceDspfParentProbeHandler(cid data) "
            "mockParentData=strcat(mockParentData data))",
            "procedure(rceDspfAnalyzerStop() "
            "mockAnalyzerStops=mockAnalyzerStops+1 "
            'printf("RCE_DSPF_BRIDGE_STOP\\n") t)',
            f'load("{helper}")',
            f'load("{gui_protocol}")',
            f'load("{launcher}")',
            f'load("{launcher}")',
            "parentPid=ipcGetPid()",
            "environment=rceDspfLaunchEnvironment(t)",
            "expectedParent=strcat(\"RCE_DSPF_PARENT_PID='\" "
            "sprintf(nil \"%d\" parentPid) \"' \" )",
            "if(and(stringp(environment) rexMatchp(expectedParent environment)) "
            'then printf("RCE_DSPF_LAUNCH_ENV_OK\\n"))',
            'parentCid=ipcBeginProcess("exec /bin/sh -c \'echo $PPID\'" "" '
            "'rceDspfParentProbeHandler)",
            "ipcWait(parentCid)",
            "if(atoi(mockParentData)==parentPid "
            'then printf("RCE_DSPF_DIRECT_PARENT_OK\\n"))',
            'first=ipcBeginProcess("sleep 30")',
            'second=ipcBeginProcess("sleep 30")',
            'rceDspfLauncherProcesses[first]="one.dspf"',
            'rceDspfLauncherProcesses[second]="two.dspf"',
            "rceDspfRegisterExitCleanup()",
            "result=rceDspfExitCleanup()",
            "if(and(result rceDspfExitCleanupRegistered mockAnalyzerStops==1 "
            "length(rceDspfLauncherProcesses)==0 !ipcIsAliveProcess(first) "
            "!ipcIsAliveProcess(second)) "
            'then printf("RCE_DSPF_EXIT_CLEANUP_OK\\n"))',
            "exit()",
        )
    )
    completed = subprocess.run(
        [dbaccess], input=skill, text=True, capture_output=True, timeout=30, check=False
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "RCE_DSPF_EXIT_CLEANUP_OK" in output
    assert "RCE_DSPF_DIRECT_PARENT_OK" in output
    assert "RCE_DSPF_LAUNCH_ENV_OK" in output
    assert output.count("RCE_DSPF_BRIDGE_STOP") == 2, output
