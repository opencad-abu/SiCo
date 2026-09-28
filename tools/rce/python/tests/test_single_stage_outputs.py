from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from rcepy.config import RceConfig


CAD_ROOT = Path(__file__).resolve().parents[3]
LVS_PYTHON = CAD_ROOT / "lvs" / "python"
if str(LVS_PYTHON) not in sys.path:
    sys.path.insert(0, str(LVS_PYTHON))

from lvspy.single_stage import SingleStageRunner  # noqa: E402
from lvspy.cli import main as lvs_main  # noqa: E402


def _config(tmp_path: Path, run_dir: Path) -> RceConfig:
    cds_lib = tmp_path / "cds.lib"
    layer_map = tmp_path / "layers.map"
    cds_lib.write_text("DEFINE sxTest /tmp/sxTest\n", encoding="utf-8")
    layer_map.write_text("# layer map\n", encoding="utf-8")
    raw = {
        "run": {"run_dir": str(run_dir), "cds_lib": str(cds_lib)},
        "input": {
            "type": "OA",
            "schematic": {"lib": "sxTest", "cell": "busTest", "view": "schematic"},
            "layout": {
                "lib": "sxTest",
                "cell": "busTest",
                "view": "layout",
                "layer_map": str(layer_map),
            },
        },
    }
    return RceConfig(raw=raw, config_path=tmp_path / "stage.toml")


@pytest.mark.parametrize(
    ("stage", "tool", "suffix", "command_file"),
    (
        ("gds", "strmout", "gds", "streamout.cmd"),
        ("cdl", "si", "cdl", "si.env"),
    ),
)
def test_single_stage_publishes_output_at_run_root_and_backs_up_old_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    tool: str,
    suffix: str,
    command_file: str,
) -> None:
    run_dir = tmp_path / suffix / "sxTest.busTest"
    run_dir.mkdir(parents=True)
    output = run_dir / f"busTest.{suffix}"
    output.write_text("old output\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / tool
    executable.write_text(
        f"#!/bin/sh\nset -eu\nprintf 'new {suffix}\\n' > busTest.{suffix}\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{Path('/usr/bin')}:{Path('/bin')}")
    monkeypatch.delenv("LVS_BACKUP_DONE", raising=False)

    runner = SingleStageRunner(_config(tmp_path, run_dir), stage)
    assert runner.run() == 0

    work_dir = run_dir / "db" / suffix
    assert output.read_text(encoding="utf-8") == f"new {suffix}\n"
    assert not (work_dir / output.name).exists()
    assert (work_dir / command_file).is_file()
    backups = list(run_dir.glob(f"{output.name}.*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "old output\n"

    manifest = run_dir / "log" / f"{'stream_gds' if stage == 'gds' else 'export_cdl'}.manifest"
    assert f"run_dir = {run_dir}" in manifest.read_text(encoding="utf-8")
    assert f"output = {output}" in manifest.read_text(encoding="utf-8")


def test_single_stage_rejects_empty_tool_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "gds" / "sxTest.busTest"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "strmout"
    executable.write_text(
        "#!/bin/sh\nset -eu\n: > busTest.gds\n", encoding="utf-8"
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{Path('/usr/bin')}:{Path('/bin')}")

    with pytest.raises(RuntimeError, match="did not create a non-empty output"):
        SingleStageRunner(_config(tmp_path, run_dir), "gds").run()

    assert not (run_dir / "busTest.gds").exists()
    assert (run_dir / "log" / "exit-abnormally").is_file()


@pytest.mark.parametrize(
    ("stage", "config_name"),
    (("gds", "stream_gds.toml"), ("cdl", "export_cdl.toml")),
)
def test_single_stage_snapshots_run_root_config(
    tmp_path: Path, stage: str, config_name: str
) -> None:
    run_dir = tmp_path / stage / "sxTest.busTest"
    run_dir.mkdir(parents=True)
    cfg = _config(tmp_path, run_dir)
    cfg = type(cfg)(cfg.to_dict(), run_dir / config_name)
    cfg.config_path.write_text("[run]\n", encoding="utf-8")

    assert SingleStageRunner(cfg, stage, generate_only=True).run() == 0

    assert cfg.config_path == run_dir / config_name
    assert cfg.config_path.read_text(encoding="utf-8") == "[run]\n"
    assert (run_dir / "log" / config_name).read_text(encoding="utf-8") == "[run]\n"


@pytest.mark.parametrize("stage", ["cdl", "gds"])
@pytest.mark.parametrize("first_mode", ["generate", "run"])
@pytest.mark.parametrize("config_location", ["run_root", "external", "log"])
def test_single_stage_reuses_original_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    first_mode: str,
    config_location: str,
) -> None:
    run_dir = tmp_path / "run"
    config_dir = {
        "run_root": run_dir,
        "external": tmp_path / "config",
        "log": run_dir / "log",
    }[config_location]
    config_dir.mkdir(parents=True)
    prefix = "export_cdl" if stage == "cdl" else "stream_gds"
    config_path = config_dir / f"{prefix}.toml"
    cds_lib = tmp_path / "cds.lib"
    cds_lib.write_text("DEFINE sxTest /tmp/sxTest\n", encoding="utf-8")
    header = config_dir / "header.cdl"
    header.write_text("* reusable header\n", encoding="utf-8")
    run_path = {"run_root": ".", "external": "../run", "log": ".."}[config_location]
    config_text = (
        f"[run]\nrun_dir = {json.dumps(run_path)}\ncds_lib = {json.dumps(str(cds_lib))}\n"
        '[input]\ntype = "OA"\n'
        '[input.schematic]\nlib = "sxTest"\ncell = "busTest"\nview = "schematic"\n'
        'cdl_header_file = "header.cdl"\n'
        '[input.layout]\nlib = "sxTest"\ncell = "busTest"\nview = "layout"\n'
    )
    config_path.write_text(config_text, encoding="utf-8")
    config_path.chmod(0o444)

    calls = []

    def fake_stage(command, **kwargs):
        calls.append((command, kwargs["cwd"]))
        (kwargs["cwd"] / f"busTest.{stage}").write_text(
            f"output {len(calls)}\n", encoding="utf-8"
        )
        return 0

    monkeypatch.setattr("lvspy.single_stage.run_stage_command", fake_stage)
    monkeypatch.delenv("LVS_BACKUP_DONE", raising=False)
    command = "export-cdl" if stage == "cdl" else "stream-gds"
    for index, mode in enumerate((first_mode, "run"), start=1):
        args = [command, str(config_path)]
        if mode == "generate":
            args.append("--generate-only")
        assert lvs_main(args) == 0
        assert config_path.read_text(encoding="utf-8") == config_text
        assert config_path.stat().st_mode & 0o777 == 0o444
        manifest = (run_dir / "log" / f"{prefix}.manifest").read_text(encoding="utf-8")
        assert f"config = {config_path}" in manifest
        if config_location == "run_root":
            assert (run_dir / "log" / config_path.name).read_text(encoding="utf-8") == config_text
        if stage == "cdl":
            assert str(header) in (run_dir / "db/cdl/si.env").read_text(encoding="utf-8")
        if mode == "run":
            output = run_dir / f"busTest.{stage}"
            assert output.read_text(encoding="utf-8") == f"output {len(calls)}\n"
            if index == 2 and first_mode == "run":
                assert any(path.read_text(encoding="utf-8") == "output 1\n"
                           for path in run_dir.glob(f"busTest.{stage}.*"))
    expected_command = ["si", "-batch"] if stage == "cdl" else [
        "strmout", "-templateFile", "streamout.cmd", "-replaceBusBitChar",
    ]
    assert calls == [(expected_command, run_dir / "db" / stage)] * (
        2 if first_mode == "run" else 1
    )
