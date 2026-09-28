from __future__ import annotations

import sys
from pathlib import Path

import pytest


PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rcepy.generators import generate_all  # noqa: E402
from rcepy.runner import RceRunner  # noqa: E402
from xrc_test_support import (  # noqa: E402
    install_fake_calibre,
    make_xrc_config,
    xrc_stages,
)


def _enable_block_cells(cfg, value: object):
    return cfg.replace('selection', value={
        "cell_enable": True,
        "cell_type": "Block Cells",
        "cells": value,
    })


def test_xrc_merges_foundry_lists_with_inline_block_cells(tmp_path: Path) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    foundry_xcells = "fab* [fab*] -I\nexisting existing -I\n"
    foundry_hcells = "fab* fab*\nexisting existing\n"
    (paths["corner_dir"] / "xcell_list").write_text(
        foundry_xcells, encoding="utf-8"
    )
    (paths["corner_dir"] / "hcell_list").write_text(
        foundry_hcells, encoding="utf-8"
    )
    cfg = _enable_block_cells(cfg, "custom custom fab_device")

    generate_all(cfg, cfg.context())

    xcells = paths["run_dir"] / "log/xcell_list.merged"
    hcells = paths["run_dir"] / "log/hcell_list.merged"
    assert xcells.read_text(encoding="utf-8") == (
        foundry_xcells + "custom\tcustom\t-I\n"
    )
    assert hcells.read_text(encoding="utf-8") == (
        foundry_hcells + "custom\tcustom\n"
    )

    stages = xrc_stages(cfg)
    assert stages[0].command[stages[0].command.index("-hcell") + 1] == str(
        hcells
    )
    assert stages[1].command[stages[1].command.index("-xcell") + 1] == str(
        xcells
    )
    assert "-full" not in stages[1].command
    assert "-incontext" not in stages[1].command


def test_xrc_accepts_one_cell_per_line_and_explicit_foundry_file(
    tmp_path: Path,
) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    (paths["corner_dir"] / "xcell_list").write_text(
        "wrong wrong -I\n", encoding="utf-8"
    )
    explicit = paths["corner_dir"] / "fab.xcells"
    explicit.write_text("base base -I\n", encoding="utf-8")
    cfg = cfg.replace('extract', 'xrc', value={"xcell_file": explicit.name})
    cell_file = tmp_path / "cells.list"
    cell_file.write_text("macro_a\nmacro_b\nmacro_a\n", encoding="utf-8")
    cfg = _enable_block_cells(cfg, str(cell_file))

    generate_all(cfg, cfg.context())

    merged = paths["run_dir"] / "log/xcell_list.merged"
    assert merged.read_text(encoding="utf-8") == (
        "base base -I\n"
        "macro_a\tmacro_a\t-I\n"
        "macro_b\tmacro_b\t-I\n"
    )


def test_xrc_can_build_user_only_xcell_list(tmp_path: Path) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    cfg = _enable_block_cells(cfg, "macro")

    generate_all(cfg, cfg.context())

    assert (paths["run_dir"] / "log/xcell_list.merged").read_text(
        encoding="utf-8"
    ) == "macro\tmacro\t-I\n"


def test_xrc_accepts_optional_source_xcell_and_single_column_hcell(
    tmp_path: Path,
) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    (paths["corner_dir"] / "xcell_list").write_text(
        "macro* -I -P\n", encoding="utf-8"
    )
    (paths["corner_dir"] / "hcell_list").write_text(
        "macro*\n", encoding="utf-8"
    )
    cfg = _enable_block_cells(cfg, "macro_a")

    generate_all(cfg, cfg.context())

    assert (paths["run_dir"] / "log/xcell_list.merged").read_text(
        encoding="utf-8"
    ) == "macro* -I -P\n"
    assert (paths["run_dir"] / "log/hcell_list.merged").read_text(
        encoding="utf-8"
    ) == "macro*\n"


@pytest.mark.parametrize(
    ("xcell", "hcell", "message"),
    [
        ("macro other -I\n", "macro macro\n", "xcell mapping"),
        ("macro macro -P\n", "macro macro\n", "xcell mapping"),
        ("macro macro -I -NOBLOCK\n", "macro macro\n", "xcell mapping"),
        ("macro macro -I\n", "macro other\n", "hcell mapping"),
    ],
)
def test_xrc_rejects_conflicting_foundry_mappings(
    tmp_path: Path,
    xcell: str,
    hcell: str,
    message: str,
) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    (paths["corner_dir"] / "xcell_list").write_text(xcell, encoding="utf-8")
    (paths["corner_dir"] / "hcell_list").write_text(hcell, encoding="utf-8")
    cfg = _enable_block_cells(cfg, "macro")

    with pytest.raises(ValueError, match=message):
        RceRunner(cfg)._stages()


def test_xrc_block_cells_require_enabled_hcell(tmp_path: Path) -> None:
    cfg, _ = make_xrc_config(tmp_path)
    cfg = cfg.replace('lvs', value={**cfg.section('lvs'), **{"hcell_enable": False, "hcell_file": ""}})
    cfg = _enable_block_cells(cfg, "macro")

    with pytest.raises(ValueError, match="requires LVS hcell to be enabled"):
        RceRunner(cfg)._stages()


def test_xrc_explicit_xcell_is_used_without_user_block_cells(tmp_path: Path) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    explicit = paths["corner_dir"] / "explicit.xcells"
    explicit.write_text("macro macro -I\n", encoding="utf-8")
    cfg = cfg.replace('extract', 'xrc', value={"xcell_file": explicit.name})

    pdb = xrc_stages(cfg)[1].command

    assert pdb[pdb.index("-xcell") + 1] == str(explicit)
    assert not (paths["run_dir"] / "log/xcell_list.merged").exists()


def test_xrc_foundry_xcell_is_used_without_user_block_cells(tmp_path: Path) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    foundry = paths["corner_dir"] / "xcell_list"
    foundry.write_text("macro macro -I\n", encoding="utf-8")

    generate_all(cfg, cfg.context())
    pdb = xrc_stages(cfg)[1].command

    assert pdb[pdb.index("-xcell") + 1] == str(foundry)
    assert not (paths["run_dir"] / "log/xcell_list.merged").exists()
    assert not (paths["run_dir"] / "log/hcell_list.merged").exists()


def test_xrc_foundry_xcell_requires_enabled_hcell(tmp_path: Path) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    (paths["corner_dir"] / "xcell_list").write_text(
        "macro macro -I\n", encoding="utf-8"
    )
    cfg = cfg.replace('lvs', value={**cfg.section('lvs'), **{"hcell_enable": False, "hcell_file": ""}})

    with pytest.raises(ValueError, match="requires LVS hcell to be enabled"):
        RceRunner(cfg)._stages()


def test_runner_writes_merged_lists_after_backing_up_old_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    (paths["corner_dir"] / "xcell_list").write_text(
        "base base -I\n", encoding="utf-8"
    )
    cfg = _enable_block_cells(cfg, "custom")
    old_log = paths["run_dir"] / "log"
    old_log.mkdir(parents=True)
    (old_log / "xcell_list.merged").write_text("stale\n", encoding="utf-8")
    calibre = install_fake_calibre(tmp_path / "mgc")
    trace = tmp_path / "calibre.trace"
    monkeypatch.setenv("MGC_HOME", str(calibre.parents[1]))
    monkeypatch.setenv("FAKE_CALIBRE_TRACE", str(trace))

    assert RceRunner(cfg).run() == 0

    merged = paths["run_dir"] / "log/xcell_list.merged"
    assert merged.read_text(encoding="utf-8") == (
        "base base -I\ncustom\tcustom\t-I\n"
    )
    assert f"-xcell {merged}" in trace.read_text(encoding="utf-8")
