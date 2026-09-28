from __future__ import annotations

import sys
from itertools import product
from pathlib import Path

import pytest

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from qrc_netlist_customize_fixtures import _command_section, _config
from rcepy.gen_qrc import generate_qrc  # noqa: E402


@pytest.mark.parametrize(
    ("coordinates", "res_layer", "res_dimensions"),
    tuple(product((False, True), repeat=3)),
)
def test_qrc_parasitic_info_boolean_combinations(
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

    command = generate_qrc(cfg, cfg.context()).read_text(encoding="utf-8")
    output_db = _command_section(command, "output_db")

    assert (
        "-output_xy parasitic_res parasitic_cap" in output_db
    ) is coordinates
    assert (
        "-include_parasitic_res_model_by_sub_conductor true" in output_db
    ) is res_layer
    assert (
        "-include_parasitic_res_length true" in output_db
    ) is res_dimensions
    assert (
        "-include_parasitic_res_width_drawn true" in output_db
    ) is res_dimensions
    assert "GENERIC" not in output_db
    assert "-include_parasitic_cap_model" not in output_db



@pytest.mark.parametrize(
    "output_type", ("dspf", "sp", "spice", "hspice", "spef", "smartview")
)
def test_qrc_coordinates_supports_documented_output_formats(
    tmp_path: Path, output_type: str
) -> None:
    cfg = _config(
        tmp_path,
        output_type=output_type,
        netlist={"parasitic_coordinates": True},
    )

    command = generate_qrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert "-output_xy parasitic_res parasitic_cap" in _command_section(
        command, "output_db"
    )



@pytest.mark.parametrize(
    "output_type",
    ("dspf", "sp", "spice", "hspice", "spef", "extview", "smartview"),
)
def test_qrc_resistor_layer_and_dimensions_support_documented_output_formats(
    tmp_path: Path, output_type: str
) -> None:
    cfg = _config(
        tmp_path,
        output_type=output_type,
        netlist={
            "parasitic_res_layer": True,
            "parasitic_res_dimensions": True,
        },
    )

    command = generate_qrc(cfg, cfg.context()).read_text(encoding="utf-8")
    output_db = _command_section(command, "output_db")

    assert "-include_parasitic_res_model_by_sub_conductor true" in output_db
    assert "-include_parasitic_res_length true" in output_db
    assert "-include_parasitic_res_width_drawn true" in output_db



def test_qrc_coordinates_rejects_extracted_view_output(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        output_type="extview",
        netlist={"parasitic_coordinates": True},
    )

    with pytest.raises(
        ValueError,
        match=r"QRC output format 'extview'.*R&C Coordinates.*smartview",
    ):
        generate_qrc(cfg, cfg.context())
