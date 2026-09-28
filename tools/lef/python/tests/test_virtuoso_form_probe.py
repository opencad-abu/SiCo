from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


CAD_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.skipif(
    os.environ.get("LEF_RUN_VIRTUOSO_PROBE") != "1",
    reason="set LEF_RUN_VIRTUOSO_PROBE=1 to instantiate the LEF form",
)
def test_lef_form_instantiates_in_virtuoso(tmp_path: Path) -> None:
    virtuoso = shutil.which(os.environ.get("LEF_VIRTUOSO", "virtuoso"))
    if virtuoso is None:
        pytest.skip("virtuoso is unavailable")

    install = tmp_path / "install"
    install.mkdir()
    (install / "tools").symlink_to(CAD_ROOT, target_is_directory=True)
    probe = tmp_path / "lef_form_probe.il"
    log = tmp_path / "virtuoso.log"
    output_lef = tmp_path / "INVX1.lef"
    launch_log = tmp_path / "lef.launch.log"
    output_lef.write_text("VERSION 5.8 ;\nEND LIBRARY\n", encoding="utf-8")
    launch_log.write_text("[LEF] Completed\n", encoding="utf-8")
    probe.write_text(
        "\n".join(
            (
                f'setShellEnvVar("CAD_HOME" "{install}")',
                f'load("{install}/tools/lef/skill++/LEF.ils")',
                "cadDisplayLefForm()",
                "probeForm=lefForm",
                "firstLefForm=lefForm",
                'if(probeForm then printf("LEF_FORM_EXISTS\\n"))',
                "if(and(probeForm probeForm~>profileFile probeForm~>profileLoad ",
                "probeForm~>profileSave probeForm~>profileSaveAs) ",
                'then printf("LEF_PROFILE_FIELDS_OK\\n"))',
                "if(and(probeForm probeForm~>moreAbstractOptions ",
                'probeForm~>extractSig) then printf("LEF_DISCLOSURE_FIELDS_OK\\n"))',
                "if(and(probeForm probeForm~>runType probeForm~>queueName ",
                "probeForm~>srvName probeForm~>lsfMonitor ",
                'probeForm~>runCpu) then printf("LEF_HARDWARE_FIELDS_OK\\n"))',
                "if(and(probeForm probeForm~>outDirType probeForm~>outDirPath) ",
                'then printf("LEF_OUTPUT_FIELDS_OK\\n"))',
                "if(and(probeForm !probeForm~>runDir) ",
                'then printf("LEF_RUN_DIR_HIDDEN_OK\\n"))',
                "if(and(probeForm !probeForm~>lsfMonitor~>enabled ",
                "hiIsIcon(SICO_lsfMonitorButtonIcon())) ",
                'then printf("LEF_LSF_MONITOR_OK\\n"))',
                "if(and(probeForm !probeForm~>moreAbstractOptions~>value) ",
                'then printf("LEF_DISCLOSURE_COLLAPSED_OK\\n"))',
                "hiFormClose(probeForm)",
                "if(and(!hiIsFormDisplayed(probeForm) lefForm==firstLefForm) ",
                'then printf("LEF_CLOSE_REOPEN_READY_OK\\n"))',
                "cadDisplayLefForm()",
                "if(and(lefForm==firstLefForm hiIsFormDisplayed(lefForm)) ",
                'then printf("LEF_FORM_REUSE_OK\\n"))',
                "probeForm=lefForm",
                "hiFormClose(probeForm)",
                "when(probeForm hiDeleteForm(probeForm))",
                "lefForm=nil",
                'summaryMeta=makeTable("lefSummaryProbe" nil)',
                'summaryMeta[\'library]="demo"',
                'summaryMeta[\'cells]="INVX1"',
                'summaryMeta[\'layoutView]="layout"',
                'summaryMeta[\'abstractView]="abstract"',
                'summaryMeta[\'bin]="Core"',
                'summaryMeta[\'optionsFile]=""',
                f'summaryMeta[\'runDir]="{tmp_path}"',
                f'summaryMeta[\'outputLef]="{output_lef}"',
                f'summaryMeta[\'launchLog]="{launch_log}"',
                'summaryMeta[\'flowLogRequest]=nil',
                'summaryMeta[\'startedAt]="probe start"',
                "if(lefSummaryDisplay(summaryMeta 0) ",
                'then printf("LEF_SUMMARY_DISPLAY_OK\\n"))',
                "firstSummaryForm=lefSummaryForm",
                "if(and(lefSummaryDisplay(summaryMeta 0) ",
                "lefSummaryForm==firstSummaryForm ",
                "hiIsFormDisplayed(lefSummaryForm)) ",
                'then printf("LEF_SUMMARY_REDISPLAY_REUSE_OK\\n"))',
                "if(and(lefSummaryForm lefSummaryForm~>lefSummaryStatus ",
                "lefSummaryForm~>lefSummaryDetails ",
                "lefSummaryForm~>lefSummarySuccess) ",
                'then printf("LEF_SUMMARY_FIELDS_OK\\n"))',
                "hiFormClose(lefSummaryForm)",
                "if(!hiIsFormDisplayed(lefSummaryForm) then ",
                'printf("LEF_SUMMARY_CLOSE_OK\\n"))',
                "if(and(lefSummaryDisplay(summaryMeta 0) ",
                "lefSummaryForm==firstSummaryForm ",
                "hiIsFormDisplayed(lefSummaryForm)) ",
                'then printf("LEF_SUMMARY_REOPEN_REUSE_OK\\n"))',
                "when(lefSummaryDisposeForm(lefSummaryForm) ",
                "lefSummaryForm=nil)",
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
        timeout=60,
        check=False,
    )
    output = completed.stdout + completed.stderr
    if log.is_file():
        output += log.read_text(encoding="utf-8", errors="replace")
    assert completed.returncode == 0, output
    assert "*Error*" not in output
    assert "LEF_FORM_EXISTS" in output
    assert "LEF_PROFILE_FIELDS_OK" in output
    assert "LEF_DISCLOSURE_FIELDS_OK" in output
    assert "LEF_HARDWARE_FIELDS_OK" in output
    assert "LEF_OUTPUT_FIELDS_OK" in output
    assert "LEF_RUN_DIR_HIDDEN_OK" in output
    assert "LEF_LSF_MONITOR_OK" in output
    assert "LEF_DISCLOSURE_COLLAPSED_OK" in output
    assert "LEF_CLOSE_REOPEN_READY_OK" in output
    assert "LEF_FORM_REUSE_OK" in output
    assert "LEF_SUMMARY_DISPLAY_OK" in output
    assert "LEF_SUMMARY_REDISPLAY_REUSE_OK" in output
    assert "LEF_SUMMARY_FIELDS_OK" in output
    assert "LEF_SUMMARY_CLOSE_OK" in output
    assert "LEF_SUMMARY_REOPEN_REUSE_OK" in output


@pytest.mark.skipif(
    os.environ.get("LEF_RUN_VIRTUOSO_PROBE") != "1",
    reason="set LEF_RUN_VIRTUOSO_PROBE=1 to exercise stale callbacks",
)
def test_lef_stale_discovery_callback_after_form_delete(tmp_path: Path) -> None:
    """A completion arriving after HI deletes its owner must be harmless."""

    virtuoso = shutil.which(os.environ.get("LEF_VIRTUOSO", "virtuoso"))
    if virtuoso is None:
        pytest.skip("virtuoso is unavailable")

    install = tmp_path / "install"
    install.mkdir()
    (install / "tools").symlink_to(CAD_ROOT, target_is_directory=True)
    result_file = tmp_path / "stale.tsv"
    probe = tmp_path / "lef_stale_callback_probe.il"
    log = tmp_path / "virtuoso.log"
    probe.write_text(
        "\n".join(
            (
                f'setShellEnvVar("CAD_HOME" "{install}")',
                f'load("{install}/tools/lef/skill++/LEF.ils")',
                "; Delete after returning to the event loop; HI forbids deletion in callbacks.",
                "procedure(lefStaleDeleteAndPost()",
                "  staleDeleted=hiDeleteForm(staleForm)",
                "  lefForm=nil",
                "  SICO_lsfDiscoveryPostFunc(staleCid 0)",
                "  if(and(staleDeleted !isFile(staleResult)",
                "         !cadLsfDiscoveryState[staleCid]) then",
                '    printf("LEF_STALE_DISCOVERY_CALLBACK_OK\\n")',
                "  else",
                '    printf("LEF_STALE_DISCOVERY_CALLBACK_FAILED deleted=%L file=%L state=%L\\n"',
                "      staleDeleted isFile(staleResult) cadLsfDiscoveryState[staleCid]))",
                "  exit()",
                ")",
                "cadDisplayLefForm()",
                "staleForm=lefForm",
                'staleForm~>runType~>value="LSF Farm"',
                "hiFormClose(staleForm)",
                "staleCid=8801",
                f'staleResult="{result_file}"',
                "SICO_guiProtocolWrite(staleResult",
                '  list(list("SELECTED_QUEUE" "normal") list("HOST" "node01"))',
                '  "ready" "1")',
                'staleState=makeTable("lefStaleDiscovery" nil)',
                "staleState['form]=staleForm",
                'staleState[\'queue]="normal"',
                "staleState['resultFile]=staleResult",
                "staleState['token]=1",
                'staleState[\'previous]=""',
                "staleState['cancelled]=nil",
                "cadLsfDiscoveryState[staleCid]=staleState",
                "putpropq(staleForm staleCid cadLsfDiscoveryCid)",
                "putpropq(staleForm 1 cadLsfHostRequestToken)",
                'hiEnqueueCmd("lefStaleDeleteAndPost()")',
                "",
            )
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [virtuoso, "-nograph", "-nocdsinit", "-replay", str(probe), "-log", str(log)],
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    output = completed.stdout + completed.stderr
    if log.is_file():
        output += log.read_text(encoding="utf-8", errors="replace")

    assert completed.returncode == 0, output
    assert "*Error*" not in output
    assert "invalid hiField" not in output
    assert "hiiListToFieldUT" not in output
    assert "Cannot delete a form" not in output
    assert "LEF_STALE_DISCOVERY_CALLBACK_OK" in output
