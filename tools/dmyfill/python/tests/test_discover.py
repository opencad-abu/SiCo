from __future__ import annotations

from pathlib import Path
import tarfile

import pytest

from dmyfillpy.discover import (
    DiscoveryError,
    describe,
    profile_data_from_deck,
    profile_from_deck,
    resolve_profile,
)
from dmyfillpy.profile import load_profile
from dmyfillpy.rule_source import resolve_fill


DECK = """\
//#DEFINE UseprBoundary
#DEFINE FILL_DM1
#DEFINE FILL_indDM1
#DEFINE FILL_DM2
#DEFINE 9K_THICK_M2
VARIABLE dm2Chip 2.5
DRC CHECK MAP DM1_FILL 31 1
DRC CHECK MAP RM1_FILL 31 7
DRC CHECK MAP indDM1_FILL 31 1
DRC CHECK MAP DM2_FILL 32 1
DRC CHECK MAP DAP_FILL 74 1
"""

LAYER_MAP = """\
M1 drawing 31 0
M2 drawing 32 0
M3 drawing 33 0
"""

RULE_TOML = """\
format = "sico.dmyfill.rules.v1"
unit = "um"
source = "test DRM"
target_density = 0.4
edge = 0.0

[groups.m1]
layers = ["M1"]
width = [0.3, 3.0]
space = 0.3
area = [0.24, 80]
tile = [0.5, 2.0]
"""


def _write_fixture(root: Path) -> tuple[Path, Path]:
    deck = root / "Dummy_Metal_Test_1.0.encrypt"
    deck.write_text(DECK, encoding="utf-8")
    layer_map = root / "tech.layermap"
    layer_map.write_text(LAYER_MAP, encoding="utf-8")
    return deck, layer_map


def test_profile_data_maps_fill_results_to_openaccess(tmp_path: Path) -> None:
    deck, layer_map = _write_fixture(tmp_path)
    document = profile_data_from_deck(deck, layer_map)
    assert document["layers"]["M1"] == {"fill_define": "FILL_DM1"}
    assert document["layers"]["M2"] == {
        "fill_define": "FILL_DM2",
        "thickness_define": "9K_THICK_M2",
    }
    assert document["rules"]["DM1_FILL"] == {"layer": "M1", "purpose": "dummy"}
    assert document["rules"]["RM1_FILL"] == {"layer": "M1", "purpose": "odummy"}
    assert document["rules"]["indDM1_FILL"] == {
        "layer": "M1",
        "purpose": "dummy",
    }
    assert "DAP_FILL" not in document["rules"]
    assert document["options"]["region_margin"] == 2.5


def test_generated_profile_is_loadable(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("SICO_DMYFILL_CACHE_DIR", str(tmp_path / "cache"))
    deck, layer_map = _write_fixture(tmp_path)
    profile = profile_from_deck(deck, layer_map)
    assert profile.layer_names == ("M1", "M2")
    assert profile.rules["DM2_FILL"].layer == "M2"
    assert load_profile(profile.path).layer_names == ("M1", "M2")


def test_dummy_rule_env_resolves_without_profile(
    tmp_path: Path, monkeypatch
) -> None:
    deck, layer_map = _write_fixture(tmp_path)
    for name in ("PROJ_PDK_HOME",):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DMYFILL_FILE", f"default,{deck}")
    monkeypatch.setenv("TECH_LAYER_MAP", str(layer_map))
    monkeypatch.setenv("SICO_DMYFILL_CACHE_DIR", str(tmp_path / "cache"))
    found = resolve_profile()
    assert found.source == "rule"
    assert found.profile.layer_names == ("M1", "M2")


def test_tarball_deck_is_extracted_and_used(
    tmp_path: Path, monkeypatch
) -> None:
    deck, layer_map = _write_fixture(tmp_path)
    archive = tmp_path / "Dummy_Metal_Test_1.0_nopdf.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        handle.add(deck, arcname=deck.name)
    monkeypatch.setenv("SICO_DMYFILL_CACHE_DIR", str(tmp_path / "cache"))
    profile = profile_from_deck(archive, layer_map)
    assert profile.deck.name == deck.name
    assert profile.layer_names == ("M1", "M2")


def test_missing_profile_reports_actionable_error(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("SICO_DMYFILL_CACHE_DIR", str(tmp_path / "cache"))
    for name in (
        "DMYFILL_FILE",
        "PROJ_PDK_HOME",
        "TECH_LAYER_MAP",
    ):
        monkeypatch.delenv(name, raising=False)
    try:
        resolve_profile()
    except DiscoveryError as exc:
        assert "DMYFILL_FILE" in str(exc)
    else:
        raise AssertionError("expected DiscoveryError")


def test_od_po_deck_is_rejected_with_clear_error(tmp_path: Path) -> None:
    deck = tmp_path / "Dummy_OD_PO_Calibre_65nm.21a"
    deck.write_text(
        "#DEFINE FILL_DOD_DPO\n"
        "DRC CHECK MAP GEN_DOD 6 1 AUTOREF\n"
        "DRC CHECK MAP GEN_DPO 17 1 AUTOREF\n",
        encoding="utf-8",
    )
    with pytest.raises(DiscoveryError, match="OD/PO"):
        profile_data_from_deck(deck, None)


def test_renamed_deck_and_layer_names_use_layer_numbers(tmp_path: Path) -> None:
    deck = tmp_path / "Dummy_Metal_Renamed.fill"
    deck.write_text(
        "#DEFINE FILL_METAL1\n"
        "DRC CHECK MAP METAL1_FILL 31 1 AUTOREF\n",
        encoding="utf-8",
    )
    layer_map = tmp_path / "renamed.layermap"
    layer_map.write_text("MET1 drawing 31 0\n", encoding="utf-8")
    document = profile_data_from_deck(deck, layer_map)
    assert document["layers"] == {"MET1": {"fill_define": "FILL_METAL1"}}
    assert document["rules"]["METAL1_FILL"] == {
        "layer": "MET1",
        "purpose": "dummy",
    }


def _write_rule_fixture(root: Path) -> tuple[Path, Path]:
    source = root / "dmyfill.toml"
    source.write_text(RULE_TOML, encoding="utf-8")
    layer_map = root / "rule.layermap"
    layer_map.write_text(LAYER_MAP, encoding="utf-8")
    return source, layer_map


def test_dmyfill_file_has_priority_over_dmyfill_toml(
    tmp_path: Path, monkeypatch
) -> None:
    deck, layer_map = _write_fixture(tmp_path)
    rule, _ = _write_rule_fixture(tmp_path)
    monkeypatch.setenv("SICO_DMYFILL_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DMYFILL_FILE", f"default,{deck}")
    monkeypatch.setenv("DMYFILL_TOML", f"default,{rule}")
    monkeypatch.setenv("TECH_LAYER_MAP", str(layer_map))
    monkeypatch.delenv("PROJ_PDK_HOME", raising=False)

    found = resolve_fill()

    assert found.rule is None
    assert found.profile is not None
    assert found.source == "rule"
    assert found.warnings == (
        "DMYFILL_TOML ignored: DMYFILL_FILE has priority",
    )
    fields = describe(found)
    assert fields["fill_source"] == "deck"


def test_invalid_dmyfill_file_does_not_fall_back_to_toml(
    tmp_path: Path, monkeypatch
) -> None:
    rule, layer_map = _write_rule_fixture(tmp_path)
    monkeypatch.setenv("DMYFILL_FILE", str(tmp_path / "missing.deck"))
    monkeypatch.setenv("DMYFILL_TOML", str(rule))
    monkeypatch.setenv("TECH_LAYER_MAP", str(layer_map))
    monkeypatch.delenv("PROJ_PDK_HOME", raising=False)

    with pytest.raises(DiscoveryError, match="DMYFILL_FILE/deck is unusable"):
        resolve_fill()


def test_invalid_dmyfill_toml_does_not_fall_back(
    tmp_path: Path, monkeypatch
) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("format = \"wrong\"\n", encoding="utf-8")
    layer_map = tmp_path / "tech.layermap"
    layer_map.write_text(LAYER_MAP, encoding="utf-8")
    monkeypatch.delenv("DMYFILL_FILE", raising=False)
    monkeypatch.setenv("DMYFILL_TOML", str(bad))
    monkeypatch.setenv("TECH_LAYER_MAP", str(layer_map))
    monkeypatch.delenv("PROJ_PDK_HOME", raising=False)

    with pytest.raises(DiscoveryError, match="format must be"):
        resolve_fill()


def test_pdk_home_dmyfill_toml_is_auto_discovered(
    tmp_path: Path, monkeypatch
) -> None:
    rule, layer_map = _write_rule_fixture(tmp_path)
    assert rule.is_file()
    monkeypatch.delenv("DMYFILL_FILE", raising=False)
    monkeypatch.delenv("DMYFILL_TOML", raising=False)
    monkeypatch.setenv("PROJ_PDK_HOME", str(tmp_path))
    monkeypatch.setenv("TECH_LAYER_MAP", str(layer_map))

    found = resolve_fill()

    assert found.source == "pdk-rule"
    assert found.rule is not None
    assert found.rule.path == rule.resolve()
    assert describe(found)["fill_source"] == "rule"
