from __future__ import annotations

from pathlib import Path

import pytest

from dmyfillpy.rules import RuleError, load_rule_spec


TOML = """\
format = "sico.dmyfill.rules.v1"
unit = "um"
source = "TN65CLDR001 2.6 8.4"
target_density = 0.40
edge = 0.0

[defaults]
wide_width = 4.5
wide_space = 1.5
blockers = { FW = 5.0, LOGO = 0.0 }

[groups.m1_mx]
layers = ["M1", "M2"]
width = [0.3, 3.0]
space = 0.3
metal_space = 0.4
area = [0.24, 80]
tile = [0.5, 2.0]
"""


def _fixture(tmp_path: Path, text: str = TOML) -> tuple[Path, Path]:
    source = tmp_path / "dmyfill.toml"
    source.write_text(text, encoding="utf-8")
    layer_map = tmp_path / "tech.layermap"
    layer_map.write_text(
        "M1 drawing 31 0\n"
        "M2 drawing 32 0\n"
        "FW drawing 95 0\n"
        "LOGO drawing 158 0\n"
        "DMEXCL dummy1 150 1\n"
        "DMEXCL dummy2 150 2\n",
        encoding="utf-8",
    )
    return source, layer_map


def test_load_rule_spec_expands_groups(tmp_path: Path) -> None:
    source, layer_map = _fixture(tmp_path)
    spec = load_rule_spec(source, layer_map)

    assert spec.source == "TN65CLDR001 2.6 8.4"
    assert spec.layer_names == ("M1", "M2")
    assert spec.rule_names == ("M1_FILL", "M2_FILL")
    assert spec.layers["M1"].metal_space == 0.4
    assert spec.layers["M2"].area_max == 80
    assert spec.rules["M1_FILL"].purpose == "dummy"


def test_unknown_top_level_field_is_rejected(tmp_path: Path) -> None:
    source, layer_map = _fixture(
        tmp_path,
        TOML.replace("target_density = 0.40", "target_density = 0.40\nmystery = 1"),
    )
    with pytest.raises(RuleError, match="unknown top-level"):
        load_rule_spec(source, layer_map)


def test_duplicate_layer_is_rejected(tmp_path: Path) -> None:
    source, layer_map = _fixture(
        tmp_path,
        TOML.replace('layers = ["M1", "M2"]', 'layers = ["M1", "M1"]'),
    )
    with pytest.raises(RuleError, match="declared more than once"):
        load_rule_spec(source, layer_map)


def test_missing_group_field_is_reported(tmp_path: Path) -> None:
    source, layer_map = _fixture(
        tmp_path,
        TOML.replace("space = 0.3\n", ""),
    )
    with pytest.raises(RuleError, match="groups.m1_mx.space is required"):
        load_rule_spec(source, layer_map)


def test_rule_source_requires_layer_map(tmp_path: Path) -> None:
    source, _ = _fixture(tmp_path)
    with pytest.raises(RuleError, match="TECH_LAYER_MAP"):
        load_rule_spec(source)


def test_rule_source_requires_um_units(tmp_path: Path) -> None:
    source, layer_map = _fixture(
        tmp_path,
        TOML.replace('unit = "um"', 'unit = "nm"'),
    )
    with pytest.raises(RuleError, match="unit must be 'um'"):
        load_rule_spec(source, layer_map)
