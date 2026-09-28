from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest
from skill_probe_support import run_virtuoso_source


CAD_ROOT = Path(__file__).resolve().parents[3]


def test_menu_registers_nl2view_everywhere() -> None:
    config = (CAD_ROOT / "utility/menu.toml").read_text(encoding="utf-8")
    generated = (CAD_ROOT / "utility/menu.generated.il").read_text(encoding="utf-8")
    form = (CAD_ROOT / "utility/skill/nl2view.il").read_text(encoding="utf-8")

    assert config.count('name = "nl2view"') == 3
    assert generated.count('list("nl2view" "Netlist to View..."') == 3
    assert 'list("Spectre" "HSPICE" "SPICE" "DSPF")' in form
    assert "PSpice" not in form
    assert 'strcat(root "/../bin/nl2view")' in form
    assert '"NL2VIEW_ORIG_LD_LIBRARY_PATH="' in form
    assert '"LD_LIBRARY_PATH=" SICO_shellQuote(SICO_pythonLibraryPath())' in form
    assert 'SICO_tempPath("nl2view.log")' not in form
    assert 'strcat(tempDir "/nl2view.log")' in form
    assert 'strcat(tempDir "/nl2view.ipc.log")' in form
    assert "'cadNl2ViewPostFunc ipcLogFile" in form
    assert "list('Apply 'cadNl2ViewApplyCB)" in form
    assert "list('Close 'cadNl2ViewCloseCB)" in form
    assert "hiSetFormButtonEnabled(form 'Apply nil)" in form
    assert "hiSetCallbackStatus" not in form
    assert 'sicoNl2ViewVersion="20260818.nl2view.v6"' in form
    assert "procedure(cadNl2ViewSessionLibraryPath" in form
    assert "ddGetObjWritePath(libObj)" in form
    assert '" --expected-library-path " SICO_shellQuote(libraryPath)' in form
    assert "ipcSignalProcess(cid 'TERM)" in form
    assert "if(isCallable('ipcSignalProcess)" in form
    assert "ipcKillProcess(cid)" in form
    assert "ddGetUpdatedLib()" in form
    assert "ddGetForcedLib()" in form
    assert "procedure(cadNl2ViewNonEmpty(value)" in form
    assert "and(stringp(value) strlen(value)>0)" in form
    assert "procedure(cadNl2ViewStamp" not in form
    assert "and(exitStatus==0 viewObj)" in form
    assert "hiDeleteForm(form)" not in form
    assert 'strcat("exec env -u PYTHONHOME -u PYTHONPATH "' in form
    assert '"-u CDS_MPS_SESSION -u CDS_MPS_HOST -u CDS_MPS_PORT " environment' in form
    assert "procedure(cadNl2ViewExitCleanup" in form
    assert "regExitBefore('cadNl2ViewExitCleanup)" in form


def test_menu_registers_read_only_lsf_monitor_everywhere() -> None:
    config = (CAD_ROOT / "utility/menu.toml").read_text(encoding="utf-8")
    generated = (CAD_ROOT / "utility/menu.generated.il").read_text(
        encoding="utf-8"
    )

    assert config.count('name = "lsfLoadMonitor"') == 3
    assert generated.count('list("lsfLoadMonitor" "LSF Load Monitor"') == 3
    assert generated.count("(SICO_lsfMonitorStart)") == 3
    assert "SICO_lsfMonitorStartSelector" not in config


def test_generated_menu_matches_toml(tmp_path: Path) -> None:
    output = tmp_path / "menu.generated.il"
    completed = subprocess.run(
        [
            shutil.which("python3") or "/usr/bin/python3",
            str(CAD_ROOT / "utility/menu_to_skill.py"),
            str(CAD_ROOT / "utility/menu.toml"),
            str(output),
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert output.read_bytes() == (CAD_ROOT / "utility/menu.generated.il").read_bytes()


@pytest.mark.skipif(
    os.environ.get("RCE_RUN_SKILL_PROBE") != "1",
    reason="set RCE_RUN_SKILL_PROBE=1",
)
def test_nl2view_form_instantiates_with_virtuoso(tmp_path: Path) -> None:
    virtuoso = shutil.which(os.environ.get("RCE_VIRTUOSO", "virtuoso"))
    if virtuoso is None:
        pytest.skip("virtuoso is unavailable")
    install = tmp_path / "install"
    install.symlink_to(CAD_ROOT.parent, target_is_directory=True)
    log = tmp_path / "virtuoso.log"
    probe = tmp_path / "nl2view_probe.il"
    probe.write_text(
        "\n".join(
            (
                f'setShellEnvVar("CAD_HOME" "{install}")',
                f'load("{CAD_ROOT.parent / "scripts/sicoAutoLoad.il"}")',
                'witMenuLoadRelative("utility/skill/nl2view.il")',
                "probe=cadNl2ViewCreateForm()",
                "if(and(probe probe~>nl2viewSource probe~>nl2viewFormat",
                "       probe~>nl2viewLibrary probe~>nl2viewCell probe~>nl2viewView",
                "       probe~>nl2viewCdsLib probe~>nl2viewCopy",
                '       probe~>nl2viewView~>value=="spectreText")',
                '  then printf("NL2VIEW_FORM_OK\\n"))',
                "when(probe hiFormDone(probe) hiDeleteForm(probe))",
                "exit()",
                "",
            )
        ),
        encoding="utf-8",
    )
    output = run_virtuoso_source(probe.read_text(), tmp_path, log_path=log)

    assert "NL2VIEW_FORM_OK" in output
