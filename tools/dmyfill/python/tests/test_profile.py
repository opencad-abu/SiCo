from __future__ import annotations

import json
from pathlib import Path

import pytest

from dmyfillpy.profile import ProfileError, load_profile


def _write(tmp_path: Path, value: dict) -> Path:
    path = tmp_path / "fill.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _valid() -> dict:
    return {
        "schema": "sico.dmyfill.profile.v1",
        "deck": "fill.cal",
        "layer_map": "fill.layermap",
        "layers": {
            "M1": {"fill_define": "FILL_DM1"},
            "M8": {
                "fill_define": "FILL_DM8",
                "thickness_define": "9K_THICK_M8",
            },
        },
        "rules": {
            "DM1_FILL": {"layer": "M1", "purpose": "dummy"},
            "RM1_FILL": {"layer": "M1", "purpose": "odummy"},
        },
        "options": {"dm_on_corner": True},
    }


def test_load_profile_resolves_deck_and_options(tmp_path: Path) -> None:
    path = _write(tmp_path, _valid())
    profile = load_profile(path)
    assert profile.deck == (tmp_path / "fill.cal").resolve()
    assert profile.layer_map == (tmp_path / "fill.layermap").resolve()
    assert profile.layer_names == ("M1", "M8")
    assert profile.dm_on_corner is True
    assert profile.rules["RM1_FILL"].purpose == "odummy"


def test_profile_rejects_unknown_rule_layer(tmp_path: Path) -> None:
    value = _valid()
    value["rules"]["DM1_FILL"]["layer"] = "M9"
    with pytest.raises(ProfileError, match="unknown layer"):
        load_profile(_write(tmp_path, value))


def test_profile_rejects_unsafe_identifiers(tmp_path: Path) -> None:
    value = _valid()
    value["layers"]["M1"]["fill_define"] = "FILL_DM1;dbCreateRect"
    with pytest.raises(ProfileError, match="deck symbol"):
        load_profile(_write(tmp_path, value))
