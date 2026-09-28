from __future__ import annotations

import sys
from pathlib import Path

import pytest

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

import rcepy.runner as runner_module  # noqa: E402
from rcepy.generators import generate_all  # noqa: E402
from rcepy.runner import RceRunner  # noqa: E402
from xrc_test_support import (  # noqa: E402
    expected_commands,
    expected_trace,
    install_fake_calibre,
    make_xrc_config,
    xrc_stages,
)


def test_xrc_enabled_stages_own_lvs_and_skip_query(tmp_path: Path) -> None:
    cfg, _ = make_xrc_config(tmp_path)

    assert cfg.is_xrc
    assert cfg.enabled_stages() == ["extract"]



def test_start_rve_is_enabled_only_for_xrc(tmp_path: Path) -> None:
    cfg, _ = make_xrc_config(tmp_path)

    assert not cfg.start_rve
    cfg = cfg.replace('extract', 'start_rve', value=True)
    assert cfg.start_rve
    cfg = cfg.replace('extract', 'tool', value="QRC")
    assert not cfg.start_rve



def test_xrc_start_rve_uses_svdb_and_detaches_from_ipc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    calibre = install_fake_calibre(tmp_path / "mgc")
    paths["svdb"].mkdir(parents=True)
    cfg = cfg.replace('extract', 'start_rve', value=True)
    launched: dict[str, object] = {}

    def fake_popen(command: list[str], **kwargs: object) -> object:
        launched["command"] = command
        launched["kwargs"] = kwargs
        return object()

    monkeypatch.setenv("MGC_HOME", str(calibre.parents[1]))
    monkeypatch.setenv("RCE_ORIG_LD_LIBRARY_PATH", "/original/eda/lib")
    launch_temp = tmp_path / ".cad"
    monkeypatch.setenv("CAD_TEMP_DIR", str(launch_temp))
    for name in ("TMPDIR", "TMP", "TEMP", "SQLITE_TMPDIR"):
        monkeypatch.setenv(name, str(launch_temp))
    monkeypatch.setattr(runner_module.subprocess, "Popen", fake_popen)

    RceRunner(cfg)._start_rve()

    assert launched["command"] == [str(calibre), "-rve", str(paths["svdb"])]
    kwargs = launched["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["cwd"] == str(paths["run_dir"] / "log")
    assert kwargs["stdin"] is runner_module.subprocess.DEVNULL
    assert kwargs["stdout"] is runner_module.subprocess.DEVNULL
    assert kwargs["stderr"] is runner_module.subprocess.DEVNULL
    assert kwargs["start_new_session"] is True
    assert kwargs["close_fds"] is True
    assert kwargs["env"]["LD_LIBRARY_PATH"] == "/original/eda/lib"
    assert all(
        name not in kwargs["env"]
        for name in ("TMPDIR", "TMP", "TEMP", "SQLITE_TMPDIR")
    )



def test_runner_plans_exact_xrc_commands_and_prefers_mgc_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    calibre = install_fake_calibre(tmp_path / "mgc")
    monkeypatch.setenv("MGC_HOME", str(calibre.parents[1]))
    generate_all(cfg, cfg.context())

    stages = xrc_stages(cfg)

    assert [stage.name for stage in stages] == ["xrc_lvs", "xrc_pdb", "xrc_fmt"]
    assert [stage.command for stage in stages] == expected_commands(calibre, paths)
    assert all(stage.cwd == paths["run_dir"] for stage in stages)
    assert all(stage.required_file == paths["runset"] for stage in stages)
    assert len({stage.log_file for stage in stages}) == 3
    assert stages[1].expected_paths == (paths["svdb"] / "pex.db",)



def test_mock_calibre_xrc_runs_serially_and_creates_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    calibre = install_fake_calibre(tmp_path / "mgc")
    trace = tmp_path / "calibre.trace"
    monkeypatch.setenv("MGC_HOME", str(calibre.parents[1]))
    monkeypatch.setenv("FAKE_CALIBRE_TRACE", str(trace))

    assert RceRunner(cfg).run() == 0

    assert trace.read_text(encoding="utf-8").splitlines() == expected_trace(paths)
    assert (paths["svdb"] / "top.sp").is_file()
    assert (paths["svdb"] / "top.phdb").is_dir()
    assert (paths["svdb"] / "top.xdb").is_dir()
    assert (paths["svdb"] / "pex.db").is_dir()
    assert paths["output"].is_file()
    assert "LVS completed. CORRECT." in (
        paths["run_dir"] / "log" / "xrc_lvs.log"
    ).read_text()
    assert "xRC Errors  =  0" in (
        paths["run_dir"] / "log" / "xrc_pdb.log"
    ).read_text()
    assert "xRC Errors  =  0" in (
        paths["run_dir"] / "log" / "xrc_fmt.log"
    ).read_text()
