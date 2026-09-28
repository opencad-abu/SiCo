from __future__ import annotations

import sys
from itertools import product
from pathlib import Path

import pytest

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rcepy.config_legacy import LegacyConfigWarning, adapt_legacy
from rcepy.gen_starrc import generate_starrc  # noqa: E402
from starrc_netlist_customize_fixtures import _config


@pytest.mark.parametrize(
    ("coordinates", "res_layer", "res_dimensions"),
    tuple(product((False, True), repeat=3)),
)
def test_starrc_maps_all_parasitic_info_combinations_for_spf(
    tmp_path: Path,
    coordinates: bool,
    res_layer: bool,
    res_dimensions: bool,
) -> None:
    cfg = _config(
        tmp_path,
        netlist={
            "parasitic_coordinates": coordinates,
            "parasitic_res_layer": res_layer,
            "parasitic_res_dimensions": res_dimensions,
        },
    )

    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")
    lines = set(command.splitlines())
    detailed = coordinates or res_layer or res_dimensions

    assert ("NETLIST_TAIL_COMMENTS: YES" in lines) is detailed
    assert ("REDUCTION: NO" in lines) is detailed
    assert ("REDUCTION: YES" in lines) is not detailed
    assert ("POWER_REDUCTION: NO" in lines) is detailed
    assert ("NETLIST_CONNECT_SECTION: YES" in lines) is coordinates
    assert ("NETLIST_NODE_SECTION: YES" in lines) is coordinates
    assert ("EXTRA_GEOMETRY_INFO: NODE RES" in lines) is coordinates
    assert ("KEEP_VIA_NODES: YES" in lines) is coordinates
    assert ("CAPACITOR_TAIL_COMMENTS: YES" in lines) is coordinates
    assert ("NETLIST_UNSCALED_COORDINATES: YES" in lines) is coordinates
    assert ("NETLIST_UNSCALED_RES_PROP: YES" in lines) is (
        coordinates or res_dimensions
    )



@pytest.mark.parametrize(
    ("res_layer", "res_dimensions"),
    tuple(product((False, True), repeat=2)),
)
def test_starrc_spef_supports_resistor_layer_and_dimensions(
    tmp_path: Path,
    res_layer: bool,
    res_dimensions: bool,
) -> None:
    cfg = _config(
        tmp_path,
        output_type="spef",
        netlist={
            "parasitic_coordinates": False,
            "parasitic_res_layer": res_layer,
            "parasitic_res_dimensions": res_dimensions,
        },
    )

    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")
    lines = set(command.splitlines())

    assert "NETLIST_FORMAT: SPEF" in lines
    assert ("NETLIST_TAIL_COMMENTS: YES" in lines) is (
        res_layer or res_dimensions
    )
    assert "CAPACITOR_TAIL_COMMENTS: YES" not in lines



@pytest.mark.parametrize(
    ("res_layer", "res_dimensions"),
    tuple(product((False, True), repeat=2)),
)
def test_starrc_rejects_incomplete_rc_coordinates_for_spef(
    tmp_path: Path,
    res_layer: bool,
    res_dimensions: bool,
) -> None:
    cfg = _config(
        tmp_path,
        output_type="spef",
        netlist={
            "parasitic_coordinates": True,
            "parasitic_res_layer": res_layer,
            "parasitic_res_dimensions": res_dimensions,
        },
    )

    with pytest.raises(
        ValueError,
        match=r"R&C coordinates.*CAPACITOR_TAIL_COMMENTS.*only for DSPF/SPF",
    ):
        generate_starrc(cfg, cfg.context())

    assert not (tmp_path / "run/log/star.cmd").exists()



def test_starrc_legacy_addon_info_uses_parasitic_info_mapping(
    tmp_path: Path,
) -> None:
    with pytest.warns(LegacyConfigWarning, match="addon_info"):
        netlist = adapt_legacy({"netlist": {"addon_info": "(t nil t)"}})["netlist"]
    cfg = _config(tmp_path, netlist=netlist)

    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")
    lines = set(command.splitlines())

    assert "EXTRA_GEOMETRY_INFO: NODE RES" in lines
    assert "CAPACITOR_TAIL_COMMENTS: YES" in lines
    assert "NETLIST_UNSCALED_RES_PROP: YES" in lines
