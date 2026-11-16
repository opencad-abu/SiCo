from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from skill_probe_support import run_virtuoso_source

CAD_ROOT = Path(__file__).resolve().parents[3]
RUN_PROBE = os.environ.get("RCE_RUN_SKILL_PROBE") == "1"


@pytest.mark.skipif(
    not RUN_PROBE,
    reason="set RCE_RUN_SKILL_PROBE=1 to run the Cadence Virtuoso probe",
)
def test_lsf_monitor_python_selection_uses_installation_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("SICO_PYTHON", "SICO_PYTHON_ROOT", "SICO_LSF_PYTHON"):
        monkeypatch.delenv(name, raising=False)
    install = tmp_path / "install"
    for directory in ("bin", "tools", "tools/common", "etc/config"):
        (install / directory).mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        CAD_ROOT.parent / "etc/config/sico-install.json",
        install / "etc/config/sico-install.json",
    )
    expected = install / "runtime/python/bin/python3"
    expected.parent.mkdir(parents=True)
    expected.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    expected.chmod(0o755)
    source = "\n".join(
        f"load({json.dumps(str(CAD_ROOT / path))})"
        for path in (
            "common/skill/SICO_environment.il",
            "common/skill/SICO_installation.il",
            "common/skill/SICO_toml.il",
            "common/skill/SICO_lsfCommands.il",
        )
    )
    source += (
        '\nprintf("LSF_PYTHON=%s\\n" SICO_lsfPythonExe())\n'
        "exit()\n"
    )
    output = run_virtuoso_source(
        source, tmp_path, env_updates={"SICO_HOME": str(install)},
        log_path=tmp_path / "lsf-python.log",
    )
    assert f"LSF_PYTHON={expected}" in output


@pytest.mark.skipif(
    not RUN_PROBE,
    reason="set RCE_RUN_SKILL_PROBE=1 to run the Cadence Virtuoso probe",
)
@pytest.mark.parametrize(
    ("entry", "display_function", "form_name"),
    (
        ("drc/skill++/DRC.ils", "cadDisplayDrcForm", "drcForm"),
        ("lvs/skill++/LVS.ils", "cadDisplayLvsForm", "lvsForm"),
        ("rce/skill++/RCE.ils", "cadDisplayRceForm", "rceForm"),
    ),
)
def test_flow_form_monitor_apply_cancel_and_stale_with_virtuoso(
    tmp_path: Path, entry: str, display_function: str, form_name: str
) -> None:
    virtuoso = shutil.which(os.environ.get("RCE_VIRTUOSO", "virtuoso"))
    if virtuoso is None:
        pytest.skip("virtuoso is unavailable")
    install = tmp_path / "install"
    install.mkdir()
    (install / "tools").symlink_to(CAD_ROOT, target_is_directory=True)
    applied = tmp_path / "applied.tsv"
    cancelled = tmp_path / "cancelled.tsv"
    stale = tmp_path / "stale.tsv"
    replay = tmp_path / "monitor-form.il"
    log = tmp_path / "monitor-form.log"
    replay.write_text(
        "\n".join(
            (
                f'setShellEnvVar("CAD_HOME" "{install}")',
                f'load("{install}/tools/{entry}")',
                f"{display_function}()",
                f"form={form_name}",
                'form~>runType~>value="LSF Farm"',
                "monitorDiscoveryCount=0",
                "procedure(SICO_lsfStartQueueDiscovery(form) "
                "monitorDiscoveryCount=add1(monitorDiscoveryCount) t)",
                f'SICO_guiProtocolWrite("{applied}" '
                'list(list("QUEUE" "normal") list("HOST" "node01")) '
                '"applied" "1")',
                'state=makeTable("flowMonitorApplied" nil)',
                "state['mode]='selector",
                "state['form]=form",
                "state['cid]=901",
                "state['token]=1",
                f'state[\'resultFile]="{applied}"',
                "state['cancelled]=nil",
                "cadLsfMonitorProcesses[901]=state",
                "putpropq(form 901 cadLsfMonitorCid)",
                "putpropq(form 1 cadLsfMonitorRequestToken)",
                "SICO_lsfMonitorPostFunc(901 0)",
                'when(and(monitorDiscoveryCount==1 '
                'get(form \'cadLsfPreferredQueue)=="normal" '
                'get(form \'cadLsfPreferredHost)=="node01") '
                'printf("FLOW_MONITOR_APPLY_OK\\n"))',
                "SICO_lsfClearPreferredValues(form)",
                'cancelState=makeTable("flowMonitorCancel" nil)',
                "cancelState['mode]='selector",
                "cancelState['form]=form",
                "cancelState['cid]=902",
                "cancelState['token]=2",
                f'cancelState[\'resultFile]="{cancelled}"',
                "cancelState['cancelled]=nil",
                "cadLsfMonitorProcesses[902]=cancelState",
                "putpropq(form 902 cadLsfMonitorCid)",
                "putpropq(form 2 cadLsfMonitorRequestToken)",
                "SICO_lsfMonitorPostFunc(902 0)",
                'when(and(monitorDiscoveryCount==1 '
                "!get(form 'cadLsfPreferredQueue) "
                "!get(form 'cadLsfPreferredHost)) "
                'printf("FLOW_MONITOR_CANCEL_OK\\n"))',
                f'SICO_guiProtocolWrite("{stale}" '
                'list(list("QUEUE" "batch")) "applied" "1")',
                'staleState=makeTable("flowMonitorStale" nil)',
                "staleState['mode]='selector",
                "staleState['form]=form",
                "staleState['cid]=903",
                "staleState['token]=3",
                f'staleState[\'resultFile]="{stale}"',
                "staleState['cancelled]=nil",
                "cadLsfMonitorProcesses[903]=staleState",
                "putpropq(form 903 cadLsfMonitorCid)",
                "putpropq(form 4 cadLsfMonitorRequestToken)",
                "SICO_lsfMonitorPostFunc(903 0)",
                'when(and(monitorDiscoveryCount==1 '
                "!get(form 'cadLsfPreferredQueue) "
                "!get(form 'cadLsfPreferredHost)) "
                'printf("FLOW_MONITOR_STALE_OK\\n"))',
                "SICO_lsfCancelFormDiscovery(form)",
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
    assert "FLOW_MONITOR_APPLY_OK" in output
    assert "FLOW_MONITOR_CANCEL_OK" in output
    assert "FLOW_MONITOR_STALE_OK" in output



@pytest.mark.skipif(
    not RUN_PROBE,
    reason="set RCE_RUN_SKILL_PROBE=1 to run the Cadence Virtuoso probe",
)
@pytest.mark.parametrize(
    ("entry", "display_function", "form_name"),
    (
        ("drc/skill++/DRC.ils", "cadDisplayDrcForm", "drcForm"),
        ("lvs/skill++/LVS.ils", "cadDisplayLvsForm", "lvsForm"),
        ("rce/skill++/RCE.ils", "cadDisplayRceForm", "rceForm"),
    ),
)
def test_monitor_selected_host_survives_real_combo_callbacks(
    tmp_path: Path, entry: str, display_function: str, form_name: str
) -> None:
    virtuoso = shutil.which(os.environ.get("RCE_VIRTUOSO", "virtuoso"))
    if virtuoso is None:
        pytest.skip("virtuoso is unavailable")
    install = tmp_path / "install"
    install.mkdir()
    (install / "tools").symlink_to(CAD_ROOT, target_is_directory=True)
    applied = tmp_path / "applied.tsv"
    replay = tmp_path / "monitor-selected-host.il"
    log = tmp_path / "monitor-selected-host.log"
    replay.write_text(
        "\n".join(
            (
                f'setShellEnvVar("CAD_HOME" "{install}")',
                f'load("{install}/tools/{entry}")',
                f"{display_function}()",
                f"form={form_name}",
                "mockLsfSerial=1000",
                "mockLsfStartCount=0",
                "procedure(SICO_lsfBeginProcess(command dataHandler "
                "errHandler postFunc) "
                "mockLsfSerial=add1(mockLsfSerial) "
                "when(postFunc=='SICO_lsfDiscoveryPostFunc "
                "mockLsfStartCount=add1(mockLsfStartCount)) "
                "mockLsfSerial)",
                "SICO_lsfCancelFormDiscovery(form)",
                "SICO_lsfUpdateQueueField(form list(\"\") \"\")",
                'form~>runType~>value="LSF Farm"',
                "SICO_lsfCancelFormDiscovery(form)",
                "SICO_lsfClearPreferredValues(form)",
                "mockLsfStartCount=0",
                f'SICO_guiProtocolWrite("{applied}" '
                'list(list("QUEUE" "normal") list("HOST" "node01")) '
                '"applied" "1")',
                'state=makeTable("selectedHostMonitor" nil)',
                "state['mode]='selector",
                "state['form]=form",
                "state['cid]=9001",
                "state['token]=1",
                f'state[\'resultFile]="{applied}"',
                "state['cancelled]=nil",
                "cadLsfMonitorProcesses[9001]=state",
                "putpropq(form 9001 cadLsfMonitorCid)",
                "putpropq(form 1 cadLsfMonitorRequestToken)",
                "SICO_lsfMonitorPostFunc(9001 0)",
                "queueCid=form~>cadLsfQueueDiscoveryCid",
                "queuePath=cadLsfQueueDiscoveryState[queueCid]['resultFile]",
                "SICO_guiProtocolWrite(queuePath "
                'list(list("QUEUE" "normal") list("QUEUE" "batch")) '
                '"ready" "1")',
                "SICO_lsfQueueDiscoveryPostFunc(queueCid 0)",
                "hostCid=form~>cadLsfDiscoveryCid",
                "hostPath=cadLsfDiscoveryState[hostCid]['resultFile]",
                "SICO_guiProtocolWrite(hostPath "
                'list(list("SELECTED_QUEUE" "normal") '
                'list("HOST" "node01") list("HOST" "node02")) '
                '"ready" "1")',
                "SICO_lsfDiscoveryPostFunc(hostCid 0)",
                "when(and(mockLsfStartCount==1 "
                'form~>queueName~>value=="normal" '
                'form~>srvName~>value=="node01" '
                "get(form 'cadLsfVerifiedHostQueue)==\"normal\" "
                "member(\"node01\" get(form 'cadLsfVerifiedHosts)) "
                "SICO_lsfSelectionValidP(form) "
                "!get(form 'cadLsfPreferredQueue) "
                "!get(form 'cadLsfPreferredHost) "
                "!get(form 'cadLsfQueueUpdateActive)) "
                'printf("MONITOR_SELECTED_HOST_OK\\n"))',
                "SICO_lsfCancelFormDiscovery(form)",
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
    assert "MONITOR_SELECTED_HOST_OK" in output
