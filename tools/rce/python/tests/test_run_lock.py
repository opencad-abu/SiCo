import subprocess
import sys

import pytest

from rcepy.run_lock import RunBusy, check, execution_lock, release, reserve
from rcepy.cli import main
from rcepy.runner import RceRunner
from test_extract_stage_outputs import _config, _install_fake_tool
from xrc_test_support import make_xrc_config


def test_cli_reservation_commands_do_not_require_config(tmp_path, capsys):
    run_dir = str(tmp_path / "run")
    assert main(["lock", "reserve", run_dir, "gui"]) == 0
    assert main(["lock", "check", run_dir, "gui"]) == 0
    assert main(["lock", "reserve", run_dir, "other"]) == 1
    assert "is reserved" in capsys.readouterr().err
    assert main(["lock", "release", run_dir, "other"]) == 1
    assert main(["lock", "release", run_dir, "gui"]) == 0
    assert main(["lock", "reserve", run_dir, "next"]) == 0
    assert main(["lock", "release", run_dir, "next"]) == 0


def test_reservation_blocks_other_launchers_and_cli_without_touching_data(tmp_path):
    cfg = _config(tmp_path, "QRC")
    cfg.run_dir.mkdir()
    config = cfg.run_dir / "rce.toml"
    config.write_text("original configuration")
    reserve(cfg.run_dir, "queued-job")
    try:
        with pytest.raises(RunBusy):
            reserve(cfg.run_dir, "second-job")
        with pytest.raises(RunBusy):
            RceRunner(cfg).run()
        with pytest.raises(RunBusy):
            release(cfg.run_dir, "second-job")
        assert config.read_text() == "original configuration"
        assert not cfg.context().log_dir.exists()
        check(cfg.run_dir, "queued-job")
    finally:
        release(cfg.run_dir, "queued-job")


def test_lock_uses_real_path_and_releases_after_exceptions(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(run, target_is_directory=True)
    with pytest.raises(ValueError):
        with execution_lock(run):
            with pytest.raises(RunBusy):
                reserve(alias, "other")
            raise ValueError("failed extraction")
    reserve(alias, "next")
    release(run, "next")


def test_reservation_survives_backend_until_publication(tmp_path, monkeypatch):
    cfg = _config(tmp_path, "QRC")
    _install_fake_tool(tmp_path, monkeypatch, "qrc", "valid")
    reserve(cfg.run_dir, "gui")
    try:
        assert RceRunner(cfg, lock_token="gui").run() == 0
        with pytest.raises(RunBusy):
            reserve(cfg.run_dir, "publication-race")
        check(cfg.run_dir, "gui")
    finally:
        release(cfg.run_dir, "gui")


def test_prepare_then_run_keeps_one_archive_and_launch_log(tmp_path, monkeypatch):
    cfg = _config(tmp_path, "QRC")
    log = cfg.run_dir / "log"
    log.mkdir(parents=True)
    (log / "old.log").write_text("old run")
    output = cfg.run_dir / "top.dspf"
    output.write_text("old netlist")
    _install_fake_tool(tmp_path, monkeypatch, "qrc", "valid")
    reserve(cfg.run_dir, "gui")
    try:
        assert RceRunner(cfg, lock_token="gui").prepare() == 0
        (log / "rce.launch.log").write_text("current launch")
        monkeypatch.setenv("RCE_BACKUP_DONE", "1")
        assert RceRunner(cfg, lock_token="gui").run() == 0
        assert (log / "rce.launch.log").read_text() == "current launch"
        assert len(list(cfg.run_dir.glob("log.*"))) == 1
        archives = list(cfg.run_dir.glob("top.dspf.*"))
        assert len(archives) == 1
        assert archives[0].read_text() == "old netlist"
        check(cfg.run_dir, "gui")
    finally:
        release(cfg.run_dir, "gui")


def test_active_tool_keeps_guard_after_runner_context_exits(tmp_path):
    process = None
    try:
        with execution_lock(tmp_path / "run") as descriptor:
            process = subprocess.Popen(
                [sys.executable, "-c", "import sys; sys.stdin.read()"],
                stdin=subprocess.PIPE,
                pass_fds=(descriptor,),
            )
        with pytest.raises(RunBusy):
            reserve(tmp_path / "run", "other")
    finally:
        if process is not None:
            process.communicate(timeout=5)
    reserve(tmp_path / "run", "next")
    release(tmp_path / "run", "next")


def test_cleanup_preserves_inputs_and_only_removes_new_known_scratch(
    tmp_path, monkeypatch
):
    cfg = _config(tmp_path, "QRC")
    run = cfg.run_dir
    cci = run / "input-cci"
    cci.mkdir(parents=True)
    (cci / "top.gds.map").write_text("input mapping")
    nets = run / "selected-nets.txt"
    nets.write_text("VDD\n")
    cfg = cfg.replace('input', 'cci', 'dir', value=str(cci))
    cfg = cfg.replace('selection', value={
        "net_enable": True,
        "net_type": "Include Nets",
        "nets": str(nets),
    })
    _install_fake_tool(tmp_path, monkeypatch, "qrc", "valid")
    runner = RceRunner(cfg)
    original = runner._run_stage

    def stage_with_scratch(stage):
        (run / "qrcTemp").mkdir()
        (run / "user-added.txt").write_text("user work")
        original(stage)

    monkeypatch.setattr(runner, "_run_stage", stage_with_scratch)
    assert runner.run() == 0
    assert (cci / "top.gds.map").read_text() == "input mapping"
    assert nets.read_text() == "VDD\n"
    assert (run / "user-added.txt").is_file()
    assert not (run / "qrcTemp").exists()


def test_prepare_rejects_inputs_under_managed_directories_before_backup(tmp_path):
    cfg = _config(tmp_path, "QRC")
    cci = cfg.run_dir / "db/cci.top"
    cci.mkdir(parents=True)
    source = cci / "mapping"
    source.write_text("original")
    cfg = cfg.replace('input', 'cci', 'dir', value=str(cci))
    with pytest.raises(ValueError, match="overlaps RCE-managed data"):
        RceRunner(cfg).prepare()
    assert source.read_text() == "original"
    assert not list(cfg.run_dir.glob("db.*"))


def test_inactive_inputs_and_literal_net_names_do_not_block_prepare(tmp_path):
    cfg = _config(tmp_path, "QRC")
    cfg = cfg.replace('input', 'gds', value={"file": str(cfg.run_dir / "db/previous.gds")})
    cfg = cfg.replace('reduction', value={"enabled": False, "selection_file": "$RCE_UNUSED/selection"})
    cfg = cfg.replace('selection', value={"net_enable": True, "nets": "$RCE_NET_LITERAL"})
    assert RceRunner(cfg).prepare() == 0


def test_prepare_rejects_input_using_output_path_before_archiving(tmp_path):
    cfg = _config(tmp_path, "QRC")
    output = cfg.run_dir / "top.dspf"
    output.parent.mkdir()
    output.write_text("source pin order")
    cfg = cfg.replace("netlist", value={
        **cfg.section("netlist"), "pin_order_enable": True,
        "pin_order_type": "User Defined File", "pin_order_file": str(output),
    })
    with pytest.raises(ValueError, match="overlaps RCE-managed data"):
        RceRunner(cfg).prepare()
    assert output.read_text() == "source pin order"
    assert not list(cfg.run_dir.glob("top.dspf.*"))


def test_xrc_corner_relative_input_is_checked_at_its_resolved_location(tmp_path):
    cfg, _ = make_xrc_config(tmp_path)
    selection = cfg.run_dir / "db/cells"
    selection.parent.mkdir(parents=True)
    selection.write_text("macro macro\n")
    cfg = cfg.replace('extract', 'xrc', value={"hcell_file": "../../run/db/cells"})
    with pytest.raises(ValueError, match="overlaps RCE-managed data"):
        RceRunner(cfg).prepare()
    assert selection.read_text() == "macro macro\n"
