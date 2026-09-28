from __future__ import annotations

from pathlib import Path

import pytest

from cadstage.command_files import (
    CdlCommandOptions,
    GdsCommandOptions,
    render_cdl_env,
    render_streamout_cmd,
)
from cadstage.backup import backup_existing
from cadstage.process import (
    publish_output,
    publish_stage_output,
    run_logged_command,
    run_stage_command,
)


def test_render_cdl_env_preserves_expected_si_fields() -> None:
    text = render_cdl_env(
        CdlCommandOptions(
            schematic_lib="lib\"quoted",
            schematic_cell="top",
            schematic_view="schematic",
            source_filename="top.cdl",
            header_file="/tmp/header.cdl",
        )
    )

    assert 'simLibName                = "lib\\"quoted"' in text
    assert 'hnlNetlistFileName        = "top.cdl"' in text
    assert 'incFILE                   = "/tmp/header.cdl"' in text
    assert "auCdlReplaceAngleBracketsWithSquare = 't" in text


def test_render_cdl_env_can_keep_angle_brackets() -> None:
    text = render_cdl_env(
        CdlCommandOptions(
            schematic_lib="lib",
            schematic_cell="top",
            schematic_view="schematic",
            source_filename="top.cdl",
            header_file="",
            replace_angle_brackets=False,
        )
    )

    assert "auCdlReplaceAngleBracketsWithSquare = 'nil" in text


def test_render_streamout_cmd_uses_explicit_output_filename() -> None:
    text = render_streamout_cmd(
        GdsCommandOptions(
            layout_lib="lib",
            layout_cell="top",
            layout_view="layout_drc",
            layer_map="/tmp/layers.map",
            output_filename="top.gds",
        )
    )

    assert 'topCell "top"' in text
    assert 'view "layout_drc"' in text
    assert 'strmFile "top.gds"' in text
    assert 'replaceBusBitChar                  "true"' in text


def test_render_streamout_cmd_can_keep_angle_brackets() -> None:
    text = render_streamout_cmd(
        GdsCommandOptions(
            layout_lib="lib",
            layout_cell="top",
            layout_view="layout",
            layer_map="/tmp/layers.map",
            output_filename="top.gds",
            replace_bus_bit_char=False,
        )
    )

    assert 'replaceBusBitChar                  "false"' in text


def test_run_logged_command_tees_output_and_returns_exit_status(
    tmp_path: Path,
) -> None:
    log_file = tmp_path / "stage.log"
    seen: list[str] = []
    result = run_logged_command(
        ["/bin/sh", "-c", "printf 'hello\\n'; exit 7"],
        cwd=tmp_path,
        log_file=log_file,
        env={},
        output_callback=seen.append,
    )

    assert result == 7
    assert seen == ["hello\n"]
    assert log_file.read_text(encoding="utf-8") == "hello\n"


def test_run_logged_command_dry_run_does_not_spawn(tmp_path: Path) -> None:
    def fail_popen(*args: object, **kwargs: object) -> object:
        raise AssertionError("dry-run must not spawn a process")

    assert (
        run_logged_command(
            ["false"],
            cwd=tmp_path,
            log_file=tmp_path / "stage.log",
            env={},
            dry_run=True,
            popen=fail_popen,  # type: ignore[arg-type]
        )
        == 0
    )
    assert not (tmp_path / "stage.log").exists()


def test_run_stage_command_checks_required_file_and_runs_shared_process(
    tmp_path: Path,
) -> None:
    command_file = tmp_path / "stage.cmd"
    command_file.write_text("command\n", encoding="utf-8")
    log_file = tmp_path / "stage.log"

    result = run_stage_command(
        ["/bin/sh", "-c", "printf 'stage\\n'"],
        label="Stream GDS",
        required_file=command_file,
        cwd=tmp_path,
        log_file=log_file,
        env={},
    )

    assert result == 0
    assert log_file.read_text(encoding="utf-8") == "stage\n"


def test_run_stage_command_reports_missing_required_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Export CDL"):
        run_stage_command(
            ["true"],
            label="Export CDL",
            required_file=tmp_path / "missing.env",
            cwd=tmp_path,
            log_file=tmp_path / "stage.log",
            env={},
        )


def test_run_stage_command_allows_stages_without_command_files(tmp_path: Path) -> None:
    assert (
        run_stage_command(
            ["/bin/sh", "-c", "exit 0"],
            label="Query",
            required_file=None,
            cwd=tmp_path,
            log_file=tmp_path / "stage.log",
            env={},
        )
        == 0
    )


def test_publish_output_rejects_empty_output(tmp_path: Path) -> None:
    produced = tmp_path / "produced.gds"
    produced.write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="non-empty output"):
        publish_output(produced, tmp_path / "published.gds")


def test_publish_output_moves_non_empty_output(tmp_path: Path) -> None:
    produced = tmp_path / "db" / "top.gds"
    published = tmp_path / "top.gds"
    produced.parent.mkdir()
    produced.write_text("gds\n", encoding="utf-8")

    publish_output(produced, published)

    assert published.read_text(encoding="utf-8") == "gds\n"
    assert not produced.exists()


def test_publish_stage_output_adds_flow_label(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="Export CDL did not create"):
        publish_stage_output(
            tmp_path / "missing.cdl",
            tmp_path / "top.cdl",
            label="Export CDL",
        )


def test_backup_existing_uses_unique_recoverable_sibling_paths(tmp_path: Path) -> None:
    first = tmp_path / "stage"
    second = tmp_path / "output.gds"
    first.mkdir()
    second.write_text("old\n", encoding="utf-8")
    (tmp_path / "stage.01-02-03-04-05").mkdir()

    backups = backup_existing((first, second), "01-02-03-04-05")

    assert backups[first].name == "stage.01-02-03-04-05.1"
    assert backups[second].name == "output.gds.01-02-03-04-05"
    assert not first.exists()
    assert backups[second].read_text(encoding="utf-8") == "old\n"
