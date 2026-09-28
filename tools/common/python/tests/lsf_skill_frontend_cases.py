from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from skill_probe_support import run_virtuoso_source

CAD_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.skipif(
    os.environ.get("RCE_RUN_SKILL_PROBE") != "1",
    reason="set RCE_RUN_SKILL_PROBE=1 to run the Cadence dbAccess probe",
)
@pytest.mark.parametrize(
    ("entry", "display_function", "start_function"),
    (
        ("drc/skill++/DRC.ils", "cadDisplayDrcForm", "drcStart"),
        ("lvs/skill++/LVS.ils", "cadDisplayLvsForm", "lvsStart"),
        ("rce/skill++/RCE.ils", "cadDisplayRceForm", "rceStart"),
        ("lef/skill++/LEF.ils", "cadDisplayLefForm", "lefStart"),
    ),
)
def test_shared_lsf_helper_loads_with_flow_frontends(
    tmp_path: Path,
    entry: str,
    display_function: str,
    start_function: str,
) -> None:
    dbaccess = shutil.which(os.environ.get("RCE_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")

    skill = "\n".join(
        (
            f'setShellEnvVar("SICO_HOME" "{CAD_ROOT.parent}")',
            'procedure(pwd() "/tmp")',
            f'load("{CAD_ROOT}/{entry}")',
            f"if(and(isCallable('{display_function}) "
            f"isCallable('{start_function}) "
            "isCallable('SICO_lsfStartHostDiscovery) "
            "isCallable('SICO_lsfStartQueueDiscovery) "
            "isCallable('SICO_lsfWrapCommand)) "
            'then printf("FLOW_SHARED_LSF_LOAD_OK\\n"))',
            "exit()",
        )
    )
    completed = subprocess.run(
        [dbaccess], input=skill, text=True, capture_output=True, timeout=30, check=False
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "*Error*" not in output
    assert "FLOW_SHARED_LSF_LOAD_OK" in output


@pytest.mark.skipif(
    os.environ.get("RCE_RUN_SKILL_PROBE") != "1",
    reason="set RCE_RUN_SKILL_PROBE=1 to run the Virtuoso form probe",
)
@pytest.mark.parametrize(
    ("entry", "display_function", "form_name"),
    (
        ("drc/skill++/DRC.ils", "cadDisplayDrcForm", "drcForm"),
        ("lvs/skill++/LVS.ils", "cadDisplayLvsForm", "lvsForm"),
        ("rce/skill++/RCE.ils", "cadDisplayRceForm", "rceForm"),
    ),
)
def test_primary_flow_forms_default_to_locked_current_host(
    tmp_path: Path, entry: str, display_function: str, form_name: str
) -> None:
    virtuoso = shutil.which(os.environ.get("RCE_VIRTUOSO", "virtuoso"))
    if virtuoso is None:
        pytest.skip("virtuoso is unavailable")
    replay = tmp_path / "current-host.il"
    log = tmp_path / "current-host.log"
    replay.write_text(
        "\n".join(
            (
                f'setShellEnvVar("SICO_HOME" "{CAD_ROOT.parent}")',
                f'load("{CAD_ROOT}/{entry}")',
                f"{display_function}()",
                f"form={form_name}",
                'when(and(form~>runType~>value=="Current Host"',
                '         form~>queueName~>value==""',
                "         !form~>queueName~>enabled",
                "         form~>srvName~>value==SICO_lsfLocalHost()",
                "         !form~>srvName~>enabled",
                "         form~>lsfMonitor",
                "         hiIsIcon(SICO_lsfMonitorButtonIcon())",
                "         !form~>lsfMonitor~>enabled)",
                '  printf("CURRENT_HOST_FIELDS_LOCKED_OK\\n"))',
                'form~>runType~>value="LSF Farm"',
                "SICO_lsfChoicesCB(form)",
                'when(and(form~>lsfMonitor~>enabled',
                "         form~>cadLsfQueueDiscoveryCid)",
                '  printf("LSF_MONITOR_BUTTON_ENABLED_OK\\n"))',
                'form~>runType~>value="Current Host"',
                "SICO_lsfChoicesCB(form)",
                'when(and(!form~>lsfMonitor~>enabled',
                "         !form~>cadLsfQueueDiscoveryCid",
                "         !form~>cadLsfMonitorCid)",
                '  printf("LSF_MONITOR_BUTTON_CANCEL_OK\\n"))',
                "when(form hiFormDone(form) hiDeleteForm(form))",
                "exit()",
                "",
            )
        ),
        encoding="utf-8",
    )
    output = run_virtuoso_source(replay.read_text(), tmp_path, log_path=log)
    assert "*Error*" not in output
    assert "(reader)" not in output
    assert "CURRENT_HOST_FIELDS_LOCKED_OK" in output
    assert "LSF_MONITOR_BUTTON_ENABLED_OK" in output
    assert "LSF_MONITOR_BUTTON_CANCEL_OK" in output
