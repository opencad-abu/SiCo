from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from skill_test_support import read_skill_source

CAD_ROOT = Path(__file__).resolve().parents[3]


def _run_dbaccess(skill: str, *, cwd: Path) -> str:
    dbaccess = shutil.which(os.environ.get("RCE_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")
    completed = subprocess.run(
        [dbaccess],
        input=skill,
        text=True,
        capture_output=True,
        timeout=40,
        check=False,
        cwd=cwd,
        env={**{key: value for key, value in os.environ.items()
                if key not in {"CAD_HOME", "CAD_TEMP_DIR", "SICO_TEMP_DIR"}},
             "SICO_HOME": str(CAD_ROOT.parent),
             "SICO_PYTHON": "/software/pkgs/python/3.9.13/bin/python3"},
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "*Error*" not in output
    assert "(reader)" not in output
    assert "still unclosed on EOF" not in output
    return output


def test_skill_temp_paths_delegate_to_the_shared_state_authority() -> None:
    helper = read_skill_source(CAD_ROOT / "common/skill/SICO_toml.il")
    loader_revision = "20260924.flow.environment.v3"
    assert "SICO_stateInitialize()" in helper
    assert "SICO_stateDirectory()" in helper
    assert "SICO_stateEnvironment()" in helper
    for relative, revision_function, revision in (
        ("drc/skill++/DRC.ils", "drcLoaderRevision", loader_revision),
        ("lvs/skill++/LVS.ils", "lvsLoaderRevision", "20260924.flow.environment.v3"),
        ("lef/skill++/LEF.ils", "lefLoaderRevision", "20260924.flow.environment.v3"),
        (
            "rce/skill++/RCE.ils",
            "rceLoaderRevision",
            "20260924.flow.environment.v3",
        ),
    ):
        loader = read_skill_source(CAD_ROOT / relative)
        assert f'{revision_function}()=="{revision}"' in loader, relative
        assert f'procedure({revision_function}()\n  "{revision}"' in loader
        assert 'SICO_tomlRevision()=="20260922.sico.temp.v6"' in loader, relative
        assert "isCallable('SICO_tempDir)" in loader, relative
        assert "isCallable('SICO_tempPath)" in loader, relative
        assert "isCallable('SICO_tempEnvironment)" in loader, relative
    assert "procedure(SICO_tempEnvironment()" in helper
    for relative, revision_function, revision in (
        ("drc/skill++/DRCCB.ils", "drcFrontendRevision", loader_revision),
        ("lvs/skill++/LVSCB.ils", "lvsFrontendRevision", "20260924.flow.environment.v3"),
        ("lef/skill++/LEFCB.ils", "lefFrontendRevision", "20260924.flow.environment.v3"),
        (
            "rce/skill++/RCECB.ils",
            "rceFrontendRevision",
            "20260924.flow.environment.v3",
        ),
        (
            "rce/skill++/RCECFG.ils",
            "rceConfigRevision",
            "20260909.pin.order.v1",
        ),
        (
            "rce/skill++/RCEGUI.ils",
            "rceGuiRevision",
            "20260909.pin.order.v1",
        ),
    ):
        source = read_skill_source(CAD_ROOT / relative)
        assert f'procedure({revision_function}()\n  "{revision}"' in source
    batch_core = read_skill_source(CAD_ROOT / "common/skill/SICO_batchCore.il")
    assert 'sicoBatchCoreVersion=="20260923.batch.commands.v3"' in batch_core
    for relative, loader_revision in (
        ("drc/skill++/DRC.ils", "drcLoaderRevision"),
        ("lvs/skill++/LVS.ils", "lvsLoaderRevision"),
        ("rce/skill++/RCE.ils", "rceLoaderRevision"),
    ):
        loader = read_skill_source(CAD_ROOT / relative)
        assert 'cadBatchCoreRevision()=="20260923.batch.commands.v3"' in loader
        entry_guard = loader.split(f"procedure({loader_revision}()", 1)[0]
        assert "isCallable('cadBatchCoreRevision)" in entry_guard, relative
        assert (
            'cadBatchCoreRevision()=="20260923.batch.commands.v3"' in entry_guard
        ), relative

    production_sources = {
        "drc/skill++/DRCCB.ils": ('SICO_tempPath("drcXXXX")',),
        "drc/skill++/DRCRUN.ils": ('SICO_tempPath(strcat("drc.pid."',),
        "drc/skill++/DRCRULESEL.ils": (
            'SICO_tempPath("drc-rule-select-inputXXXX")',
            'SICO_tempPath("drc-rule-select-resultXXXX")',
            "initialFile=makeTempFileName(initialFile)",
            "outputFile=makeTempFileName(outputFile)",
        ),
        "lvs/skill++/LVSCB.ils": ('SICO_tempPath("lvsXXXX")',),
        "lvs/skill++/LVSRUN.ils": ('SICO_tempPath(strcat("lvs.pid."',),
        "lvs/skill++/STAGECB.ils": (
            'if(stage=="gds" then "lvsgdsXXXX" else "lvscdlXXXX")',
        ),
        "lef/skill++/LEFCB.ils": ('SICO_tempPath("lefXXXX")',),
        "lef/skill++/LEFRUN.ils": ('SICO_tempPath(strcat("lef.pid."',),
        "common/skill/SICO_profile.il": (
            'SICO_tempPath("cad-profile-loadXXXX")',
            'SICO_tempPath("cad-profile-saveXXXX")',
            "dataFile=makeTempFileName(dataFile)",
        ),
        "rce/skill++/RCECB.ils": ('SICO_tempPath("rceXXXX")',),
    }
    for relative, expected_fragments in production_sources.items():
        source = read_skill_source(CAD_ROOT / relative)
        for expected in expected_fragments:
            assert expected in source, relative
        assert "getTempDir()" not in source, relative

    for relative in (
        "drc/skill++/DRCCB.ils",
        "lvs/skill++/LVSCB.ils",
        "lvs/skill++/STAGECB.ils",
        "lef/skill++/LEFCB.ils",
        "rce/skill++/RCECB.ils",
    ):
        source = read_skill_source(CAD_ROOT / relative)
        assert "tempFile=makeTempFileName(tempFile)" in source, relative
    rce_callback = read_skill_source(CAD_ROOT / "rce/skill++/RCECB.ils")
    assert 'SICO_tempPath(strcat("rce.pid."' in rce_callback

    for relative in (
        "drc/skill++/DRCRUN.ils",
        "lvs/skill++/LVSRUN.ils",
        "lef/skill++/LEFRUN.ils",
        "rce/skill++/RCECB.ils",
        "rce/skill++/RCEBATCH.ils",
        "common/skill/SICO_batchCore.il",
    ):
        source = read_skill_source(CAD_ROOT / relative)
        assert "SICO_tempEnvironment()" in source, relative
    gui_protocol = read_skill_source(CAD_ROOT / "common/skill/SICO_guiProtocol.il")
    dspf_launcher = read_skill_source(CAD_ROOT / "rce/skill/RCE_dspfLauncher.il")
    assert "SICO_tempEnvironment()" in gui_protocol
    assert "SICO_guiPythonEnvironment(" in dspf_launcher


def test_cad_temp_helper_stays_at_launch_cwd_with_dbaccess(tmp_path: Path) -> None:
    helper = CAD_ROOT / "common/skill/SICO_toml.il"
    launch_dir = tmp_path / "launch"
    changed_dir = tmp_path / "changed"
    launch_dir.mkdir()
    changed_dir.mkdir()
    expected_dir = launch_dir / ".sico"
    expected_file = expected_dir / "probe.tmp"
    skill = "\n".join(
        (
            f'load("{helper}")',
            f'changeWorkingDir("{changed_dir}")',
            'printf("CAD_TEMP_REV=%s\\n" SICO_tomlRevision())',
            'printf("CAD_TEMP_DIR=%s\\n" SICO_tempDir())',
            'printf("CAD_TEMP_FILE=%s\\n" SICO_tempPath("probe.tmp"))',
            'printf("CAD_TEMP_ESCAPE=%L\\n" SICO_tempPath("../escape"))',
            'printf("CAD_TEMP_ENV=%s\\n" SICO_tempEnvironment())',
            "exit()",
        )
    )
    output = _run_dbaccess(skill, cwd=launch_dir)
    assert "CAD_TEMP_REV=20260922.sico.temp.v6" in output
    assert f"CAD_TEMP_DIR={expected_dir}" in output
    assert f"CAD_TEMP_FILE={expected_file}" in output
    assert "CAD_TEMP_ESCAPE=nil" in output
    line = next(line.split("CAD_TEMP_ENV=", 1)[1] for line in output.splitlines()
                if "CAD_TEMP_ENV=" in line)
    assignments = dict(item.split("=", 1) for item in shlex.split(line) if "=" in item)
    for name in ("SICO_TEMP_DIR", "TMPDIR", "TMP", "TEMP", "SQLITE_TMPDIR"):
        assert assignments[name] == str(expected_dir)
    for name in ("TMPDIR", "TMP", "TEMP", "SQLITE_TMPDIR", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
        assert f"SICO_ORIG_{name}_SET" in assignments
    assert assignments["XDG_CACHE_HOME"] == str(expected_dir / "cache")
    assert assignments["XDG_RUNTIME_DIR"] == str(expected_dir / "runtime")
    assert "PYTHONDONTWRITEBYTECODE=1" in output
    assert expected_dir.is_dir()
    assert expected_dir.stat().st_mode & 0o777 == 0o700
    assert (expected_dir / "cache").is_dir()
    assert (expected_dir / "runtime").stat().st_mode & 0o777 == 0o700


def test_rce_reduction_skill_loads_with_dbaccess(tmp_path: Path) -> None:
    reduction = CAD_ROOT / "rce/skill++/RCEREDUCTION.ils"
    skill = "\n".join(
        (
            f'load("{reduction}")',
            'printf("RCE_REDUCTION_REV=%s\\n" rceReductionRevision())',
            "exit()",
        )
    )

    output = _run_dbaccess(skill, cwd=tmp_path)

    assert "RCE_REDUCTION_REV=20260908.recognize.gates.v1" in output


def test_cad_temp_helper_hot_reload_rejects_unsafe_directory_without_chmod(
    tmp_path: Path,
) -> None:
    helper = CAD_ROOT / "common/skill/SICO_toml.il"
    launch_dir = tmp_path / "launch"
    launch_dir.mkdir()
    expected_dir = launch_dir / ".sico"
    expected_dir.mkdir(mode=0o777)
    expected_dir.chmod(0o777)
    skill = "\n".join(
        (
            'cadTomlVersion="20260817.temp.boundary.v3"',
            'procedure(SICO_tempDir() "STALE")',
            f'load("{helper}")',
            'printf("CAD_TEMP_REV=%s\\n" SICO_tomlRevision())',
            'printf("TEMP_REJECTED=%L\\n" null(errset(SICO_tempDir() nil)))',
            "exit()",
        )
    )

    output = _run_dbaccess(skill, cwd=launch_dir)

    assert "CAD_TEMP_REV=20260922.sico.temp.v6" in output
    assert "TEMP_REJECTED=t" in output
    assert expected_dir.stat().st_mode & 0o777 == 0o777
