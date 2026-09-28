from __future__ import annotations

import sys
from pathlib import Path

import pytest

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rcepy.gen_qrc import generate_qrc  # noqa: E402
from selection_and_filters_fixtures import _config


@pytest.mark.parametrize(
    ("mode", "expected_extracts"),
    (
        (
            "Include Nets",
            (
                'extract -extract_via_cap true -extract_gate_diffusion_fringing_cap true -selection "nets_file {nets}" -type "rc_coupled"',
            ),
        ),
        (
            "Exclude Nets",
            (
                'extract -extract_via_cap true -extract_gate_diffusion_fringing_cap true -selection "all" -type rc_coupled',
                'extract -selection "nets_file {nets}" -type "none"',
            ),
        ),
    ),
)
def test_qrc_generates_include_and_exclude_net_files(
    tmp_path: Path,
    mode: str,
    expected_extracts: tuple[str, ...],
) -> None:
    source = tmp_path / "nets.list"
    source.write_text("clk bus<3> clk\n", encoding="utf-8")
    cfg = _config(
        tmp_path,
        "QRC",
        selection={"net_enable": True, "net_type": mode, "nets": source.name},
    )

    command = generate_qrc(cfg, cfg.context()).read_text(encoding="utf-8")
    generated = tmp_path / "run/log/nets"

    assert generated.read_text(encoding="utf-8") == "clk\nbus[3]\n"
    for expected in expected_extracts:
        assert expected.format(nets=generated) in command
    assert command.count("\nextract ") == len(expected_extracts)



def test_qrc_generates_white_block_cell_file(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        "QRC",
        selection={
            "cell_enable": True,
            "cell_type": "Block Cells",
            "cells": "std_a, std_b std_a",
        },
    )

    command = generate_qrc(cfg, cfg.context()).read_text(encoding="utf-8")
    generated = tmp_path / "run/log/cells"

    assert generated.read_text(encoding="utf-8") == "std_a\nstd_b\n"
    assert "-parasitic_blocking_device_cells_type white" in command
    assert f'-parasitic_blocking_device_cells_file "{generated}"' in command
    assert "graybox -type layout" not in command
    assert command.count("extraction_setup ") == 1
    assert command.index("-parasitic_blocking_device_cells_file") < command.index(
        "\nextract "
    )



def test_qrc_converts_relative_capacitance_percent_to_ratio(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "QRC", filters={"cap_percentage": "0.1"})

    command = generate_qrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert "filter_coupling_cap -coupling_cap_threshold_relative 0.001" in command
