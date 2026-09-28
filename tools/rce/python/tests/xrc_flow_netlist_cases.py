from __future__ import annotations

import sys
from pathlib import Path

import pytest

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rcepy.generators import generate_all  # noqa: E402
from xrc_test_support import (  # noqa: E402
    make_xrc_config,
    xrc_stages,
)


def test_xrc_translates_filter_selection_and_netlist_options(tmp_path: Path) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    cfg = cfg.replace('extract', 'name_source', value="layout")
    cfg = cfg.replace('filter', value={
        "cap_percentage": "0.1",
        "cap_value": "0.01",
        "res_value": "0.001",
    })
    cfg = cfg.replace('selection', value={
        "net_enable": True,
        "net_type": "Include Nets",
        "nets": ["VDD", "X1/OUT"],
    })
    cfg = cfg.replace('netlist', value={
        "parasitic_coordinates": True,
        "parasitic_res_layer": True,
        "parasitic_res_dimensions": True,
        "hierarchy_delimiter_enable": True,
        "hierarchy_delimiter": "/",
        "dspf_remove_instances": "TRUE",
    })

    generate_all(cfg, cfg.context())
    text = paths["runset"].read_text(encoding="utf-8")

    assert "PEX REDUCE CC ABSOLUTE 0.01 AND RATIO 0.001" in text
    assert "PEX REDUCE MINRES SHORT 0.001" in text
    assert 'PEX EXTRACT INCLUDE LAYOUTNAMES "VDD" "X1/OUT"' in text
    assert "SEPARATOR \"/\"" in text
    assert "CLOCATION LOCATION RLAYER RLENGTH RLOCATION RWIDTH" in text
    assert "NOINSTANCESECTION" in text
    assert "-cselect" in xrc_stages(cfg)[1].command
    cfg = cfg.replace('extract', 'rc_type', value="RC")
    assert "-select" in xrc_stages(cfg)[1].command



@pytest.mark.parametrize(
    ("output_type", "svrf_format", "suffix"),
    [
        ("dspf", "DSPF", "dspf"),
        ("sp", "HSPICE", "sp"),
        ("spice", "HSPICE", "sp"),
    ],
)
def test_xrc_netlist_formats_select_svrf_format_and_suffix(
    tmp_path: Path, output_type: str, svrf_format: str, suffix: str
) -> None:
    cfg, paths = make_xrc_config(tmp_path, output_type=output_type)

    generate_all(cfg, cfg.context())

    text = paths["runset"].read_text(encoding="utf-8")
    assert f'PEX NETLIST "{paths["run_dir"] / "db" / f"top.{suffix}"}"' in text
    assert f" {svrf_format} SOURCENAMES" in text



@pytest.mark.parametrize("output_type", ["hspice", "spef", "spectre"])
def test_xrc_rejects_formats_outside_the_rce_contract(
    tmp_path: Path, output_type: str
) -> None:
    cfg, _ = make_xrc_config(tmp_path, output_type=output_type)

    with pytest.raises(
        ValueError, match="Calibre XRC output supports only.*view, dspf, and spice"
    ):
        generate_all(cfg, cfg.context())



@pytest.mark.parametrize(
    ("coordinates", "res_layer", "res_dimensions", "expected"),
    [
        (False, False, False, ()),
        (False, False, True, ("RLENGTH", "RWIDTH")),
        (False, True, False, ("RLAYER",)),
        (False, True, True, ("RLAYER", "RLENGTH", "RWIDTH")),
        (True, False, False, ("CLOCATION", "LOCATION", "RLOCATION")),
        (
            True,
            False,
            True,
            ("CLOCATION", "LOCATION", "RLENGTH", "RLOCATION", "RWIDTH"),
        ),
        (
            True,
            True,
            False,
            ("CLOCATION", "LOCATION", "RLAYER", "RLOCATION"),
        ),
        (
            True,
            True,
            True,
            (
                "CLOCATION",
                "LOCATION",
                "RLAYER",
                "RLENGTH",
                "RLOCATION",
                "RWIDTH",
            ),
        ),
    ],
)
def test_xrc_parasitic_info_flag_combinations(
    tmp_path: Path,
    coordinates: bool,
    res_layer: bool,
    res_dimensions: bool,
    expected: tuple[str, ...],
) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    cfg = cfg.replace('netlist', value={
        "parasitic_coordinates": coordinates,
        "parasitic_res_layer": res_layer,
        "parasitic_res_dimensions": res_dimensions,
    })

    generate_all(cfg, cfg.context())

    output_statement = next(
        line
        for line in paths["runset"].read_text(encoding="utf-8").splitlines()
        if line.startswith('PEX NETLIST "')
    )
    tokens = output_statement.split()
    parasitic_keywords = {
        "LOCATION",
        "RLOCATION",
        "CLOCATION",
        "RLAYER",
        "RWIDTH",
        "RLENGTH",
    }
    assert tuple(token for token in tokens if token in parasitic_keywords) == expected



@pytest.mark.parametrize("output_type", ["dspf", "sp", "spice"])
def test_xrc_supported_formats_emit_all_parasitic_info_options(
    tmp_path: Path, output_type: str
) -> None:
    cfg, paths = make_xrc_config(tmp_path, output_type=output_type)
    cfg = cfg.replace('netlist', value={
        "parasitic_coordinates": True,
        "parasitic_res_layer": True,
        "parasitic_res_dimensions": True,
    })

    generate_all(cfg, cfg.context())

    output_statement = next(
        line
        for line in paths["runset"].read_text(encoding="utf-8").splitlines()
        if line.startswith('PEX NETLIST "')
    )
    assert (
        "CLOCATION LOCATION RLAYER RLENGTH RLOCATION RWIDTH"
        in output_statement
    )



@pytest.mark.parametrize("rc_type", ["C", "CC", "Cg", "Cg+Cc"])
def test_xrc_capacitance_only_rejects_unavailable_capacitor_locations(
    tmp_path: Path, rc_type: str
) -> None:
    cfg, _ = make_xrc_config(tmp_path)
    cfg = cfg.replace('extract', 'rc_type', value=rc_type)
    cfg = cfg.replace('netlist', value={"parasitic_coordinates": True})

    with pytest.raises(
        ValueError,
        match=r"CLOCATION.*capacitance-only \(-c\).*parasitic_coordinates",
    ):
        generate_all(cfg, cfg.context())



@pytest.mark.parametrize(
    ("replace_type", "character_map", "bus_delimiter"),
    [
        ("<> ===> []", 'PEX NETLIST CHARACTER MAP "<[>]"', "[]"),
        ("[] ===> <>", 'PEX NETLIST CHARACTER MAP "[<]>"', "<>"),
    ],
)
@pytest.mark.parametrize(
    ("output_type", "has_bus_delimiter"),
    [
        ("dspf", True),
        ("sp", False),
        ("spice", False),
    ],
)
def test_xrc_bracket_replacement_uses_native_character_map(
    tmp_path: Path,
    replace_type: str,
    character_map: str,
    bus_delimiter: str,
    output_type: str,
    has_bus_delimiter: bool,
) -> None:
    cfg, paths = make_xrc_config(tmp_path, output_type=output_type)
    cfg = cfg.replace('netlist', value={
        "brackets_replace": True,
        "brackets_replace_type": replace_type,
    })

    generate_all(cfg, cfg.context())

    lines = paths["runset"].read_text(encoding="utf-8").splitlines()
    output_statement = next(
        line
        for line in lines
        if line.startswith("PEX NETLIST ") and "CHARACTER MAP" not in line
    )
    assert lines.count(character_map) == 1
    assert "CHARACTER MAP" not in output_statement
    bus_option = f'BUSDELIM "{bus_delimiter}"'
    assert (bus_option in output_statement) is has_bus_delimiter



def test_xrc_rejects_unknown_bracket_replacement(tmp_path: Path) -> None:
    cfg, _ = make_xrc_config(tmp_path)
    cfg = cfg.replace('netlist', value={
        "brackets_replace": True,
        "brackets_replace_type": "{} ===> []",
    })

    with pytest.raises(ValueError, match="brackets replacement type"):
        generate_all(cfg, cfg.context())
