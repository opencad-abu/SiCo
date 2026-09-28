from __future__ import annotations

import sys
from pathlib import Path

import pytest


PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rcepy.config import RceConfig  # noqa: E402
from rcepy.runner import RceRunner  # noqa: E402


TOOLS = (("QRC", "qrc"), ("StarRC", "StarXtract"))


def _config(tmp_path: Path, tool: str, *, output_type: str = "dspf") -> RceConfig:
    run_dir = tmp_path / "run"
    tech_dir = tmp_path / "tech"
    corner_dir = tech_dir / "Cmax"
    corner_dir.mkdir(parents=True)
    if tool == "QRC":
        (corner_dir / "qrcTechFile").write_text("mock qrc tech\n", encoding="utf-8")
    else:
        (corner_dir / "nxtgrd").write_text("mock nxtgrd\n", encoding="utf-8")
        (corner_dir / "tran.map").write_text("mock mapping\n", encoding="utf-8")
    runset = tmp_path / "rce_lvs.cal"
    runset.write_text("// RCE LVS rules\n", encoding="utf-8")

    return RceConfig(
        raw={
            "run": {"run_dir": str(run_dir)},
            "input": {
                "type": "CCI",
                "cci": {"cell": "top", "dir": str(tmp_path / "cci.top")},
            },
            "lvs": {"tool": "Calibre", "runset_file": str(runset)},
            "extract": {
                "tool": tool,
                "tech_dir": str(tech_dir),
                "corner": "Cmax",
                "rc_type": "RC",
                "output_type": output_type,
            },
            "netlist": {"output_path": str(run_dir / "top.dspf")},
        },
        config_path=tmp_path / "rce.toml",
    )


def _install_fake_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    executable_name: str,
    behavior: str,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    executable = bin_dir / executable_name
    executable.write_text(
        """#!/bin/sh
set -eu
case "$FAKE_EXTRACT_BEHAVIOR" in
  no_output) ;;
  empty)
    mkdir -p "$(dirname "$FAKE_EXTRACT_OUTPUT")"
    : > "$FAKE_EXTRACT_OUTPUT"
    ;;
  valid)
    mkdir -p "$(dirname "$FAKE_EXTRACT_OUTPUT")"
    printf '* fresh extracted netlist\\n' > "$FAKE_EXTRACT_OUTPUT"
    ;;
  *) exit 64 ;;
esac
exit 0
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("FAKE_EXTRACT_BEHAVIOR", behavior)
    monkeypatch.setenv("FAKE_EXTRACT_OUTPUT", str(tmp_path / "run" / "top.dspf"))


@pytest.mark.parametrize(("tool", "executable_name"), TOOLS)
@pytest.mark.parametrize(
    ("behavior", "error"),
    (
        ("no_output", "did not create expected output"),
        ("empty", "did not create a non-empty output"),
    ),
)
def test_extract_exit_zero_without_nonempty_netlist_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    executable_name: str,
    behavior: str,
    error: str,
) -> None:
    _install_fake_tool(tmp_path, monkeypatch, executable_name, behavior)

    with pytest.raises(RuntimeError, match=error):
        RceRunner(_config(tmp_path, tool)).run()

    marker = tmp_path / "run" / "log" / "exit-abnormally"
    assert error in marker.read_text(encoding="utf-8")


@pytest.mark.parametrize(("tool", "executable_name"), TOOLS)
def test_extract_exit_zero_with_nonempty_netlist_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    executable_name: str,
) -> None:
    _install_fake_tool(tmp_path, monkeypatch, executable_name, "valid")

    assert RceRunner(_config(tmp_path, tool)).run() == 0
    assert (tmp_path / "run" / "top.dspf").read_text(encoding="utf-8") == (
        "* fresh extracted netlist\n"
    )


def test_completed_run_preserves_preexisting_root_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _config(tmp_path, "QRC")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    cfg = type(cfg)(cfg.to_dict(), run_dir / "rce.toml")
    cfg = cfg.replace("vendor", "path", value="relative.dat")
    cfg.config_path.write_text("[run]\n", encoding="utf-8")
    (run_dir / "scratch.txt").write_text("temporary\n", encoding="utf-8")
    (run_dir / "top.dspf.pre_brackets").write_text("backup\n", encoding="utf-8")
    (run_dir / "extra.dspf").write_text("preserve\n", encoding="utf-8")
    (run_dir / "db.previous").mkdir()
    (run_dir / "tool-work").mkdir()
    _install_fake_tool(tmp_path, monkeypatch, "qrc", "valid")

    assert RceRunner(cfg).run() == 0

    assert cfg.config_path == run_dir / "rce.toml"
    assert cfg.path("vendor", "path") == str(run_dir / "relative.dat")
    assert {entry.name for entry in run_dir.iterdir()} == {
        "db",
        "log",
        "top.dspf",
        "extra.dspf",
        "scratch.txt", "top.dspf.pre_brackets", "db.previous", "tool-work",
    }
    assert (run_dir / "log/rce.toml").read_text(encoding="utf-8") == "[run]\n"
    assert f"config = {run_dir / 'log/rce.toml'}" in (
        run_dir / "log/rce.manifest"
    ).read_text(encoding="utf-8")


def test_completed_run_preserves_timestamped_previous_run_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _config(tmp_path, "QRC")
    run_dir = tmp_path / "run"
    old_db = run_dir / "db"
    old_log = run_dir / "log"
    old_output = run_dir / "top.dspf"
    old_db.mkdir(parents=True)
    old_log.mkdir()
    (old_db / "old.db").write_text("old database\n", encoding="utf-8")
    (old_log / "old.log").write_text("old log\n", encoding="utf-8")
    old_output.write_text("* old extracted netlist\n", encoding="utf-8")

    # Emulate an archive made by the SKILL launcher during an earlier run.
    skill_db_backup = run_dir / "db.09-07-12-34-56-12345"
    skill_log_backup = run_dir / "log.09-07-12-34-56-12345"
    skill_output_backup = run_dir / "top.dspf.09-07-12-34-56-12345"
    skill_db_backup.mkdir()
    skill_log_backup.mkdir()
    skill_output_backup.write_text("* older netlist\n", encoding="utf-8")
    (run_dir / "db.previous").mkdir()

    _install_fake_tool(tmp_path, monkeypatch, "qrc", "valid")

    assert RceRunner(cfg).run() == 0

    python_db_backups = list(run_dir.glob("db.??-??-??-??-??"))
    python_log_backups = list(run_dir.glob("log.??-??-??-??-??"))
    python_output_backups = list(run_dir.glob("top.dspf.??-??-??-??-??"))
    assert len(python_db_backups) == 1
    assert len(python_log_backups) == 1
    assert len(python_output_backups) == 1
    assert (python_db_backups[0] / "old.db").read_text(encoding="utf-8") == (
        "old database\n"
    )
    assert (python_log_backups[0] / "old.log").read_text(encoding="utf-8") == (
        "old log\n"
    )
    assert python_output_backups[0].read_text(encoding="utf-8") == (
        "* old extracted netlist\n"
    )
    assert skill_db_backup.is_dir()
    assert skill_log_backup.is_dir()
    assert skill_output_backup.read_text(encoding="utf-8") == "* older netlist\n"
    assert (run_dir / "db.previous").exists()
    assert old_output.read_text(encoding="utf-8") == "* fresh extracted netlist\n"


def test_completed_run_preserves_explicit_non_dspf_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _config(tmp_path, "QRC", output_type="spef")
    run_dir = tmp_path / "run"
    output = run_dir / "top.spef"
    cfg = cfg.replace('netlist', 'output_path', value=str(output))
    _install_fake_tool(tmp_path, monkeypatch, "qrc", "valid")
    monkeypatch.setenv("FAKE_EXTRACT_OUTPUT", str(output))

    assert RceRunner(cfg).run() == 0

    assert output.read_text(encoding="utf-8") == "* fresh extracted netlist\n"
    assert {entry.name for entry in run_dir.iterdir()} == {"db", "log", "top.spef"}


def test_failed_run_preserves_root_artifacts_for_diagnosis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    scratch = run_dir / "scratch.txt"
    scratch.write_text("diagnostic\n", encoding="utf-8")
    _install_fake_tool(tmp_path, monkeypatch, "qrc", "no_output")

    with pytest.raises(RuntimeError, match="did not create expected output"):
        RceRunner(_config(tmp_path, "QRC")).run()

    assert scratch.read_text(encoding="utf-8") == "diagnostic\n"


def test_generate_only_preserves_root_config_and_artifacts(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "QRC")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    cfg = type(cfg)(cfg.to_dict(), run_dir / "rce.toml")
    cfg.config_path.write_text("[run]\n", encoding="utf-8")
    scratch = run_dir / "scratch.txt"
    scratch.write_text("generated\n", encoding="utf-8")

    assert RceRunner(cfg, generate_only=True).run() == 0

    assert cfg.config_path == run_dir / "rce.toml"
    assert cfg.config_path.is_file()
    assert scratch.is_file()
    assert f"config = {cfg.config_path}" in (
        run_dir / "log/rce.manifest"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize(("tool", "executable_name"), TOOLS)
def test_extract_rejects_an_unmodified_old_netlist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    executable_name: str,
) -> None:
    output = tmp_path / "run" / "top.dspf"
    output.parent.mkdir(parents=True)
    output.write_text("* stale netlist\n", encoding="utf-8")
    _install_fake_tool(tmp_path, monkeypatch, executable_name, "no_output")
    monkeypatch.setenv("RCE_BACKUP_DONE", "1")

    with pytest.raises(RuntimeError, match="did not update expected output"):
        RceRunner(_config(tmp_path, tool)).run()


@pytest.mark.parametrize(("tool", "_"), TOOLS)
@pytest.mark.parametrize("output_type", ("extview", "smartview"))
def test_view_outputs_are_not_treated_as_netlist_files(
    tmp_path: Path, tool: str, _: str, output_type: str
) -> None:
    stage = RceRunner(_config(tmp_path, tool, output_type=output_type))._extract_stage()

    assert stage.expected_paths == ()
    assert not stage.require_nonempty_outputs
