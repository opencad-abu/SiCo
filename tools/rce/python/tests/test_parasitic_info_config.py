from __future__ import annotations

import sys
from itertools import product
from pathlib import Path

import pytest


PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rcepy.config import RceConfig  # noqa: E402
from rcepy.config_legacy import adapt_legacy, LegacyConfigWarning


def _config(tmp_path: Path, netlist: dict[str, object], *, legacy=False) -> RceConfig:
    return RceConfig(
        raw=adapt_legacy({"netlist": netlist}) if legacy else {"netlist": netlist},
        config_path=tmp_path / "rce.toml",
    )


@pytest.mark.parametrize("flags", tuple(product((False, True), repeat=3)))
def test_named_parasitic_info_flags_are_read_independently(
    tmp_path: Path, flags: tuple[bool, bool, bool]
) -> None:
    cfg = _config(
        tmp_path,
        {
            "parasitic_coordinates": flags[0],
            "parasitic_res_layer": flags[1],
            "parasitic_res_dimensions": flags[2],
        },
    )

    assert cfg.parasitic_info_flags() == flags


@pytest.mark.parametrize("flags", tuple(product((False, True), repeat=3)))
def test_legacy_addon_info_keeps_all_boolean_combinations(
    tmp_path: Path, flags: tuple[bool, bool, bool]
) -> None:
    words = ["t" if enabled else "nil" for enabled in flags]
    with pytest.warns(LegacyConfigWarning, match="netlist.addon_info"):
        cfg = _config(tmp_path, {"addon_info": f"({' '.join(words)})"}, legacy=True)

    assert cfg.parasitic_info_flags() == flags


def test_named_flag_overrides_only_its_legacy_position(tmp_path: Path) -> None:
    with pytest.warns(LegacyConfigWarning, match="netlist.addon_info"):
        cfg = _config(
            tmp_path,
            {"addon_info": [True, True, False], "parasitic_res_layer": False},
            legacy=True,
        )

    assert cfg.parasitic_info_flags() == (True, False, False)


@pytest.mark.parametrize(
    "netlist",
    (
        {"addon_info": "(t maybe nil)"},
        {"addon_info": "(t nil nil t)"},
        {"parasitic_coordinates": "sometimes"},
    ),
)
def test_invalid_parasitic_info_values_are_rejected(
    tmp_path: Path, netlist: dict[str, object]
) -> None:
    with pytest.raises(ValueError, match="boolean|exactly three"):
        _config(tmp_path, netlist, legacy=True).parasitic_info_flags()
