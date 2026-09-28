"""Failure and ownership contracts for run artifact publication and cleanup."""

from dataclasses import replace

import pytest
import rcepy.output_publication as publication
import rcepy.run_artifacts as artifacts
import rcepy.runner as runner_module
from rcepy.runner import RceRunner
from xrc_test_support import make_xrc_config


def test_cleanup_unlinks_scratch_symlink_without_touching_target(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    target = tmp_path / "external"
    target.mkdir()
    (target / "input").write_text("keep")
    scratch = run_dir / "qrcTemp"
    scratch.symlink_to(target, target_is_directory=True)
    artifacts.clean_completed_run(run_dir, set())
    assert not scratch.exists()
    assert (target / "input").read_text() == "keep"


def test_cleanup_uses_protected_output_roots_and_initial_names(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    monkeypatch.setenv("SICO_TEMP_DIR", str(run_dir / ".sico"))
    keep = artifacts.protected_root_names(
        run_dir, managed_names=("db", "log"), initial_root_names={"qrcTemp"},
        output_paths=(run_dir / "_xrc_out.cal_/result.sp", tmp_path / "external.sp"),
    )
    assert keep == {"db", "log", "qrcTemp", "_xrc_out.cal_", ".sico"}
    for name in (*keep, "_xrc_tmp.cal_"):
        (run_dir / name).mkdir(exist_ok=True)
    artifacts.clean_completed_run(run_dir, keep)
    assert {p.name for p in run_dir.iterdir()} == keep


def test_config_is_archived_and_manifest_updated_before_scratch_removal(tmp_path, monkeypatch):
    cfg, _ = make_xrc_config(tmp_path)
    runner = RceRunner(cfg)
    runner.ctx.log_dir.mkdir(parents=True, exist_ok=True)
    config = runner.ctx.run_dir / "rce.toml"
    config.write_text("configuration")
    runner = RceRunner(replace(cfg, config_path=config))
    scratch = runner.ctx.run_dir / "qrcTemp"
    scratch.mkdir()
    original = artifacts.clean_completed_run

    def clean(run_dir, keep_names):
        archived = runner.ctx.log_dir / "rce.toml"
        assert not config.exists()
        assert archived.read_text() == "configuration"
        assert f"config = {archived}" in (runner.ctx.log_dir / "rce.manifest").read_text()
        original(run_dir, keep_names)

    monkeypatch.setattr(runner_module, "clean_completed_run", clean)
    runner._cleanup_run_root()
    assert not scratch.exists()


def test_manifest_failure_preserves_scratch_and_writes_failure_marker(tmp_path, monkeypatch):
    cfg, _ = make_xrc_config(tmp_path)
    runner = RceRunner(cfg)
    runner.ctx.log_dir.mkdir(parents=True, exist_ok=True)
    config = runner.ctx.run_dir / "rce.toml"
    config.write_text("configuration")
    runner = RceRunner(replace(cfg, config_path=config))
    scratch = runner.ctx.run_dir / "qrcTemp"
    scratch.mkdir()

    def fail_manifest(*args, **kwargs):
        raise OSError("manifest denied")

    monkeypatch.setattr(runner_module, "write_run_manifest", fail_manifest)
    with pytest.raises(RuntimeError, match="Cannot clean completed run directory.*manifest denied"):
        runner._cleanup_run_root()
    assert scratch.exists()
    assert "manifest denied" in (runner.ctx.log_dir / "exit-abnormally").read_text()
    assert (runner.ctx.log_dir / "rce.toml").read_text() == "configuration"


def test_missing_publication_does_not_overwrite_previous_output(tmp_path):
    target = tmp_path / "top.sp"
    target.write_text("prior output")
    with pytest.raises(RuntimeError, match="Cannot publish missing extracted netlist"):
        publication.publish_outputs(((tmp_path / "absent.sp", target),), lambda text: None)
    assert target.read_text() == "prior output"


def test_failed_publication_keeps_both_files_and_emits_no_success(tmp_path, monkeypatch):
    native, target = tmp_path / "native.sp", tmp_path / "stable.sp"
    native.write_text("new")
    target.write_text("prior")
    messages = []

    def fail_replace(source, destination):
        raise OSError("replace denied")

    monkeypatch.setattr(publication.os, "replace", fail_replace)
    with pytest.raises(RuntimeError, match="Cannot publish extracted netlist.*replace denied"):
        publication.publish_outputs(((native, target),), messages.append)
    assert native.read_text() == "new"
    assert target.read_text() == "prior"
    assert messages == []
