from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
CAD_ROOT = ROOT.parent
LAUNCHER = ROOT / "skill" / "MTS_netlistor.il"
REVISION = "20260924.sico.mts.launcher.v7"


def _skill_string(value: Path) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _cadence_output(completed: subprocess.CompletedProcess[str], log: Path) -> str:
    output = completed.stdout + completed.stderr
    if log.is_file():
        output += log.read_text(encoding="utf-8", errors="replace")
    return output


def test_launcher_source_guards_all_definitions_and_uses_skill_gets_api() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    assert f'mtsNetlistorVersion=="{REVISION}"' in source
    assert "isCallable('mtsNetlistorStart))" in source
    assert f'mtsNetlistorVersion="{REVISION}"' in source
    assert "line=gets(line input)" in source
    assert "line=gets(input)" not in source
    assert "procedure(mtsNetlistorDigestValidP" in source
    assert 'strlen(value)==64' in source
    assert 'rexMatchp("^[0-9a-fA-F]+$" value)' in source
    assert 'rexMatchp("^[0-9a-fA-F]{64}$"' not in source
    assert source.rfind(f'mtsNetlistorVersion="{REVISION}"') < source.rfind("\n)")
    assert source.rfind("procedure(mtsNetlistorStart") < source.rfind(
        f'mtsNetlistorVersion="{REVISION}"'
    )
    assert "ipcActivateBatch(cid)" in source
    assert "'mtsNetlistorDataHandler nil" in source
    assert "'mtsNetlistorPostFunc ipcLogPath" in source
    assert "return(cid)" in source
    assert "procedure(mtsNetlistorRunningProcess" in source
    assert "procedure(mtsNetlistorBrand()" in source
    assert 'info("<INFO> %s::MTS Netlistor is already running.\\n"' in source
    assert "mtsNetlistorBrand()" in source
    assert 'SICO_guiPythonLaunchCommand(command "MTS_NETLISTOR_ORIG_LD_LIBRARY_PATH"' in source
    assert 'command=strcat("env SICO_PYTHON="' in source
    assert '"-u LD_PRELOAD -u LD_AUDIT -u CDS_MPS_SESSION -u CDS_MPS_HOST -u CDS_MPS_PORT "' in source
    assert 'deOpenCellView(library cell view "" nil "r")' in source
    assert 'member(view list("symbol" "spectreText" "spiceText"))' in source
    assert "evalstring" not in source.lower()


def test_menu_callback_is_revision_aware_everywhere() -> None:
    config = (CAD_ROOT / "utility" / "menu.toml").read_text(encoding="utf-8")
    generated = (CAD_ROOT / "utility" / "menu.generated.il").read_text(
        encoding="utf-8"
    )

    assert config.count('name = "mtsNetlistor"') == 3
    assert config.count(f'(mtsNetlistorRevision)==\\"{REVISION}\\"') == 3
    assert generated.count('list("mtsNetlistor" "MTS Netlistor..."') == 3
    assert generated.count(f'(mtsNetlistorRevision)==\\\"{REVISION}\\\"') == 3


@pytest.mark.skipif(
    os.environ.get("MTS_RUN_SKILL_PROBE") != "1",
    reason="set MTS_RUN_SKILL_PROBE=1 after sourcing the qualified Cadence setup",
)
def test_launcher_double_load_and_file_readers_with_dbaccess(tmp_path: Path) -> None:
    dbaccess = shutil.which(os.environ.get("MTS_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")
    cds_lib = Path(os.environ.get("MTS_TEST_CDSLIB", "/workarea/xh/smic28/cds.lib"))
    if not cds_lib.is_file():
        pytest.skip(f"test cds.lib is unavailable: {cds_lib}")

    token = tmp_path / "token.txt"
    token.write_text("launcher-token ignored\n", encoding="ascii")
    probe = tmp_path / "launcher_probe.il"
    log = tmp_path / "dbaccess.log"
    probe.write_text(
        "\n".join(
            (
                f"load({_skill_string(LAUNCHER)})",
                f"load({_skill_string(LAUNCHER)})",
                'printf("MTS_REVISION=%s\\n" mtsNetlistorRevision())',
                'printf("MTS_START=%L\\n" isCallable(\'mtsNetlistorStart))',
                'printf("MTS_PROC_START=%L\\n" mtsNetlistorProcStartTime(ipcGetPid()))',
                'printf("MTS_DIGEST=%L\\n" mtsNetlistorDigestValidP("d57f20996c8c3c46fe03af2a3e6fe21fc84b637d27dc35a2e9f946ef19f3d006"))',
                'printf("MTS_OPEN_VALID=%L\\n" car(mtsNetlistorParseOpenView("MTS_OPEN_VIEW\\ttarget_lib\\ttop\\tspectreText")))',
                'printf("MTS_OPEN_BAD_VIEW=%L\\n" car(mtsNetlistorParseOpenView("MTS_OPEN_VIEW\\ttarget_lib\\ttop\\tlayout")))',
                'printf("MTS_OPEN_INJECT=%L\\n" car(mtsNetlistorParseOpenView("MTS_OPEN_VIEW\\ttarget_lib\\ttop) hiQuit()\\tsymbol")))',
                f'printf("MTS_TOKEN=%L\\n" mtsNetlistorReadToken({_skill_string(token)}))',
                "exit()",
                "",
            )
        ),
        encoding="ascii",
    )
    completed = subprocess.run(
        [dbaccess, "-cdslib", str(cds_lib), "-load", str(probe), "-log", str(log)],
        cwd=tmp_path,
        check=False,
        text=True,
        capture_output=True,
        timeout=80,
    )
    output = _cadence_output(completed, log)

    assert completed.returncode == 0, output
    assert "*Error*" not in output
    assert "redefined" not in output
    assert f"MTS_REVISION={REVISION}" in output
    assert "MTS_START=t" in output
    assert "MTS_PROC_START=nil" not in output
    assert "MTS_DIGEST=t" in output
    assert "MTS_OPEN_VALID=t" in output
    assert "MTS_OPEN_BAD_VIEW=nil" in output
    assert "MTS_OPEN_INJECT=nil" in output
    assert 'MTS_TOKEN="launcher-token"' in output
