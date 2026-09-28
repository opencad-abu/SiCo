from __future__ import annotations

import sys
from pathlib import Path

import pytest


PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rcepy.config import RceConfig  # noqa: E402
from rcepy.gen_qrc import generate_qrc  # noqa: E402
from rcepy.qrc_cells import QRC_BLOCK_CELL_ENV  # noqa: E402


def _config(
    tmp_path: Path,
    process_dir: Path,
    *,
    corner: str = "Typ",
    cells: str | None = None,
) -> RceConfig:
    raw: dict[str, object] = {
        "run": {"run_dir": str(tmp_path / "run")},
        "input": {
            "type": "CCI",
            "cci": {"dir": str(tmp_path / "input.cci"), "cell": "top"},
        },
        "lvs": {"tool": "Calibre"},
        "extract": {
            "tool": "QRC",
            "tech_dir": str(process_dir),
            "corner": corner,
            "temperature": "25",
            "rc_type": "R+Cg+Cc",
            "output_type": "dspf",
        },
        "runtime": {"ext_cpus": "1"},
        "netlist": {},
    }
    if cells is not None:
        raw["selection"] = {
            "cell_enable": True,
            "cell_type": "Block Cells",
            "cells": cells,
        }
    return RceConfig(raw=raw, config_path=tmp_path / "rce.toml")


def _write_corner(
    process_dir: Path,
    name: str = "Typ",
) -> Path:
    corner_dir = process_dir / name
    corner_dir.mkdir(parents=True)
    (corner_dir / "qrcTechFile").write_text("technology\n", encoding="utf-8")
    return corner_dir


def _generate(cfg: RceConfig) -> str:
    return generate_qrc(cfg, cfg.context()).read_text(encoding="utf-8")


def _blocking_file_option(path: Path) -> str:
    return f'-parasitic_blocking_device_cells_file "{path}"'


def test_qrc_does_not_guess_foundry_filename_when_env_is_unset(
    tmp_path: Path,
) -> None:
    process_dir = tmp_path / "QRC"
    corner_dir = _write_corner(process_dir)
    (corner_dir / "preserveCellList.txt").write_text(
        "fab_macro*\n", encoding="utf-8"
    )
    cfg = _config(tmp_path, process_dir)

    command = _generate(cfg)

    assert "-parasitic_blocking_device_cells_file" not in command
    assert "-parasitic_blocking_device_cells_type" not in command
    assert "graybox -type layout" not in command
    assert not (tmp_path / "run/log/cells.merged").exists()
    assert not (tmp_path / "run/log/cells").exists()


def test_qrc_uses_absolute_configured_foundry_file_without_user_cells(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process_dir = tmp_path / "QRC"
    corner_dir = _write_corner(process_dir)
    (corner_dir / "preserveCellList.txt").write_text(
        "wrong_default*\n", encoding="utf-8"
    )
    configured = tmp_path / "shared/vendor.blocks"
    configured.parent.mkdir()
    configured.write_text("fab_macro*\n", encoding="utf-8")
    monkeypatch.setenv(QRC_BLOCK_CELL_ENV, str(configured))
    cfg = _config(tmp_path, process_dir)

    command = _generate(cfg)

    assert _blocking_file_option(configured) in command
    assert "preserveCellList.txt" not in command
    assert "-parasitic_blocking_device_cells_type white" in command
    assert not (tmp_path / "run/log/cells.merged").exists()


def test_qrc_merges_relative_configured_file_and_user_cells(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process_dir = tmp_path / "QRC"
    foundry_text = "fab_*\n# keep this foundry comment\nfab_exact\n+ c_only\n\n"
    corner_dir = _write_corner(process_dir)
    foundry_file = corner_dir / "lists/vendor.blocks"
    foundry_file.parent.mkdir()
    foundry_file.write_text(foundry_text, encoding="utf-8")
    monkeypatch.setenv(QRC_BLOCK_CELL_ENV, "lists/vendor.blocks")
    cfg = _config(
        tmp_path,
        process_dir,
        cells="fab_exact fab_child user_macro fab_* second_macro user_macro",
    )

    command = _generate(cfg)
    merged_file = tmp_path / "run/log/cells.merged"

    assert merged_file.read_text(encoding="utf-8") == (
        foundry_text + "user_macro\nsecond_macro\n"
    )
    assert _blocking_file_option(merged_file) in command
    assert "preserveCellList.txt" not in command
    assert not (tmp_path / "run/log/cells").exists()


def test_qrc_keeps_user_only_cell_file_behavior_without_foundry_file(
    tmp_path: Path,
) -> None:
    process_dir = tmp_path / "QRC"
    _write_corner(process_dir)
    cfg = _config(tmp_path, process_dir, cells="user_a user_b user_a")

    command = _generate(cfg)
    user_file = tmp_path / "run/log/cells"

    assert user_file.read_text(encoding="utf-8") == "user_a\nuser_b\n"
    assert _blocking_file_option(user_file) in command
    assert not (tmp_path / "run/log/cells.merged").exists()


def test_qrc_resolves_relative_env_below_case_resolved_corner_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process_dir = tmp_path / "QRC"
    corner_dir = _write_corner(process_dir, "Typ")
    configured = corner_dir / "fabBlockCells.list"
    configured.write_text("fab_macro\n", encoding="utf-8")
    monkeypatch.setenv(QRC_BLOCK_CELL_ENV, configured.name)
    cfg = _config(tmp_path, process_dir, corner="tYP")

    command = _generate(cfg)

    assert _blocking_file_option(configured) in command
    assert str(process_dir / "tYP" / configured.name) not in command


def test_qrc_uses_existing_empty_configured_foundry_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process_dir = tmp_path / "QRC"
    corner_dir = _write_corner(process_dir)
    configured = corner_dir / "empty.blocks"
    configured.write_text("", encoding="utf-8")
    monkeypatch.setenv(QRC_BLOCK_CELL_ENV, configured.name)
    cfg = _config(tmp_path, process_dir)

    command = _generate(cfg)

    assert _blocking_file_option(configured) in command
    assert "-parasitic_blocking_device_cells_type white" in command
    assert "graybox -type layout" not in command
    assert not (tmp_path / "run/log/cells.merged").exists()


def test_qrc_treats_whitespace_env_as_unset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process_dir = tmp_path / "QRC"
    corner_dir = _write_corner(process_dir)
    (corner_dir / "preserveCellList.txt").write_text(
        "fab_macro*\n", encoding="utf-8"
    )
    monkeypatch.setenv(QRC_BLOCK_CELL_ENV, "   ")
    cfg = _config(tmp_path, process_dir)

    command = _generate(cfg)

    assert "-parasitic_blocking_device_cells_file" not in command


def test_qrc_rejects_missing_configured_file_without_filename_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process_dir = tmp_path / "QRC"
    corner_dir = _write_corner(process_dir)
    (corner_dir / "preserveCellList.txt").write_text(
        "fab_macro*\n", encoding="utf-8"
    )
    missing = corner_dir / "missing.blocks"
    monkeypatch.setenv(QRC_BLOCK_CELL_ENV, missing.name)
    cfg = _config(tmp_path, process_dir)

    with pytest.raises(
        FileNotFoundError,
        match=rf"{QRC_BLOCK_CELL_ENV}.*{missing.name}",
    ):
        _generate(cfg)

    assert not (tmp_path / "run/log/qrc.ccl").exists()
    assert not (tmp_path / "run/log/cells.merged").exists()
