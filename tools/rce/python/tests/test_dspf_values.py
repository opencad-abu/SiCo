from __future__ import annotations

import math

import pytest

from rcepy.dspf.values import layer_attribute, parse_attributes, parse_spice_number


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("1", 1.0),
        ("-2.5e-3", -2.5e-3),
        ("3K", 3.0e3),
        ("4MEG", 4.0e6),
        ("5M", 5.0e-3),
        ("6u", 6.0e-6),
        ("7PF", 7.0e-12),
        ("1F", 1.0e-15),
        ("8KOHM", 8.0e3),
        ("9OHM", 9.0),
        ("2MEGOHM", 2.0e6),
        ("10V", 10.0),
    ],
)
def test_parse_spice_number_normalizes_engineering_suffixes(
    token: str, expected: float
) -> None:
    assert parse_spice_number(token) == pytest.approx(expected)


@pytest.mark.parametrize("token", ["", "nan", "inf", "1MOO", "1XYZ", "1e999"])
def test_parse_spice_number_rejects_invalid_or_nonfinite_values(token: str) -> None:
    with pytest.raises(ValueError):
        parse_spice_number(token)


def test_parse_attributes_preserves_dspf_geometry_and_layer_forms() -> None:
    attributes = parse_attributes(
        "// $l=2u $w=.5u $lvl=7 TC1=.001 $M4 layer1=M2 layer2=M3"
    )

    assert attributes["l"] == "2u"
    assert attributes["w"] == ".5u"
    assert attributes["tc1"] == ".001"
    assert attributes["m4"] is True
    bounds = parse_attributes("// $llx=1 $lly=2")
    assert bounds["llx"] == "1"
    assert bounds["lly"] == "2"
    assert "ll" not in bounds
    assert layer_attribute(attributes) == "7"
    assert math.isclose(parse_spice_number(str(attributes["l"])), 2e-6)


def test_layer_attribute_combines_two_capacitor_layers() -> None:
    assert layer_attribute(parse_attributes("// layer1=M2 layer2=M3")) == "M2:M3"
