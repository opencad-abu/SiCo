from __future__ import annotations

import sys
from pathlib import Path

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))



from selection_and_filters_fixtures import _config


def test_names_or_file_sources_deduplicate_inline_names_in_order(
    tmp_path: Path,
) -> None:
    cfg = _config(
        tmp_path,
        "QRC",
        selection={
            "net_enable": True,
            "net_type": "Include Nets",
            "nets": "clk, data clk bus<3> data",
            "cell_enable": True,
            "cell_type": "Block Cells",
            "cells": ["std_a", "std_b", "std_a", "std_b"],
        },
    )

    assert cfg.net_selection() == ("include", ["clk", "data", "bus<3>"])
    assert cfg.blocked_cells() == ["std_a", "std_b"]



def test_names_or_file_sources_expand_files_comments_commas_and_duplicates(
    tmp_path: Path,
) -> None:
    names = tmp_path / "selected.names"
    names.write_text(
        "# full-line comment\nclk, data clk # trailing comment\nbus<3> data\n",
        encoding="utf-8",
    )
    cfg = _config(
        tmp_path,
        "QRC",
        selection={
            "net_enable": True,
            "net_type": "Exclude Nets",
            "nets": names.name,
            "cell_enable": True,
            "cell_type": "Block Cells",
            "cells": names.name,
        },
    )

    expected = ["clk", "data", "bus<3>"]
    assert cfg.net_selection() == ("exclude", expected)
    assert cfg.blocked_cells() == expected



def test_legacy_net_selection_keys_are_ignored(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        "QRC",
        legacy={
            "EXT_NET_ENABLE": True,
            "EXT_NET_TYPE": "Advance",
            "EXT_NETS": "clk",
        },
    )

    assert cfg.net_selection() == (None, [])



def test_legacy_cell_selection_keys_are_ignored(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        "QRC",
        legacy={
            "EXT_CELL_ENABLE": True,
            "EXT_CELL_TYPE": "Hier Cells",
            "EXT_CELLS": "std_a",
        },
    )

    assert cfg.blocked_cells() == []
