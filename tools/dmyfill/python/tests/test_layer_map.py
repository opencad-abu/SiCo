from __future__ import annotations

from pathlib import Path

import pytest

from dmyfillpy.layer_map import LayerMapError, load_layer_map


def test_layer_map_parses_drawing_and_dummy_entries(tmp_path: Path) -> None:
    source = tmp_path / "tech.layermap"
    source.write_text(
        "# layer purpose stream datatype\n"
        "M1 drawing 31 0\n"
        "M2 drawing 32 0\n"
        "DMEXCL dummy1 150 1\n",
        encoding="utf-8",
    )
    layer_map = load_layer_map(source)

    assert layer_map.require("M1").stream == 31
    assert layer_map.require("DMEXCL", "dummy1").datatype == 1
    with pytest.raises(LayerMapError, match="M3/drawing"):
        layer_map.require("M3")


def test_duplicate_layer_map_entry_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "tech.layermap"
    source.write_text(
        "M1 drawing 31 0\n"
        "M1 drawing 31 1\n",
        encoding="utf-8",
    )
    with pytest.raises(LayerMapError, match="duplicate"):
        load_layer_map(source)


def test_non_mapping_lines_are_ignored(tmp_path: Path) -> None:
    source = tmp_path / "tech.layermap"
    source.write_text(
        "# comment\n"
        "this is not a layer line\n"
        "M1 drawing 31 0\n",
        encoding="utf-8",
    )
    assert load_layer_map(source).require("M1").stream == 31
