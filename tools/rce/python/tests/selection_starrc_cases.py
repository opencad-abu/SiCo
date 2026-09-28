from __future__ import annotations

import sys
from pathlib import Path

import pytest

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rcepy.gen_starrc import generate_starrc  # noqa: E402
from selection_and_filters_fixtures import _config


@pytest.mark.parametrize(
    ("mode", "expected_file"),
    (
        ("Include Nets", "NETS: clk bus<3>\n"),
        ("Exclude Nets", "NETS: * !clk !bus<3>\n"),
    ),
)
def test_starrc_generates_net_and_block_cell_selection_files(
    tmp_path: Path,
    mode: str,
    expected_file: str,
) -> None:
    cfg = _config(
        tmp_path,
        "StarRC",
        selection={
            "net_enable": True,
            "net_type": mode,
            "nets": "clk, bus<3> clk",
            "cell_enable": True,
            "cell_type": "Block Cells",
            "cells": "std_a std_b std_a",
        },
    )

    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")
    nets_file = tmp_path / "run/log/star.nets"
    cells_file = tmp_path / "run/log/star.cells"

    assert nets_file.read_text(encoding="utf-8") == expected_file
    assert cells_file.read_text(encoding="utf-8") == "SKIP_CELLS: std_a std_b\n"
    assert f"NETS_FILE: {nets_file}" in command
    assert f"SKIP_CELLS_FILE: {cells_file}" in command
    assert "NET_TYPE: SCHEMATIC" in command
    assert "CELL_TYPE: LAYOUT" in command



@pytest.mark.parametrize("setting", ("NETS: other", "NETS_FILE: other.nets"))
def test_starrc_rejects_common_options_that_change_user_net_selection(
    tmp_path: Path,
    setting: str,
) -> None:
    cfg = _config(
        tmp_path,
        "StarRC",
        selection={
            "net_enable": True,
            "net_type": "Include Nets",
            "nets": "clk",
        },
    )
    (tmp_path / "tech/RCmax/common.opt").write_text(
        f"{setting}\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match=r"user net selection.*NETS"):
        generate_starrc(cfg, cfg.context())



def test_starrc_merges_foundry_and_user_block_cells_cumulatively(
    tmp_path: Path,
) -> None:
    cfg = _config(
        tmp_path,
        "StarRC",
        selection={
            "cell_enable": True,
            "cell_type": "Block Cells",
            "cells": "user_macro",
        },
    )
    (tmp_path / "tech/RCmax/common.opt").write_text(
        "SKIP_CELLS: fab_macro\n", encoding="utf-8"
    )

    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert "SKIP_CELLS_FILE:" in command
    assert command.endswith("\nSKIP_CELLS: fab_macro\n")



def test_starrc_rejects_nested_common_net_selection(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        "StarRC",
        selection={
            "net_enable": True,
            "net_type": "Exclude Nets",
            "nets": "clk",
        },
    )
    included = tmp_path / "foundry-selection.opt"
    included.write_text("NETS: *\n", encoding="utf-8")
    (tmp_path / "tech/RCmax/common.opt").write_text(
        f"INCLUDE_FILE: {included}\n", encoding="utf-8"
    )

    with pytest.raises(
        ValueError, match=r"user net selection.*foundry-selection[.]opt"
    ):
        generate_starrc(cfg, cfg.context())



def test_starrc_converts_filter_units_and_emits_minres(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        "StarRC",
        filters={
            "cap_value": "2.5",
            "cap_percentage": "0.1",
            "res_value": "0.001",
        },
    )

    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert "COUPLING_ABS_THRESHOLD: 0.0000000000000025" in command
    assert "COUPLING_REL_THRESHOLD: 0.001" in command
    assert "COUPLING_THRESHOLD_OPERATION: AND" in command
    assert "NETLIST_MINRES_HANDLING: SHORT" in command
    assert "NETLIST_MINRES_THRESHOLD: 0.001" in command
