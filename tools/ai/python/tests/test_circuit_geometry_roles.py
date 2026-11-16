"""Port roles and the documented left-edge band order (power/ground/bidir/input)."""

from __future__ import annotations

from circuit_geometry_fixture import sample_geometry

from cadai.circuit_geometry import preview_geometry
from cadai.circuit_spec_plan import preview_circuit


def _ports(args, rows):
    """rows: (name, net, direction, role, xy, escape); role None omits the field."""
    args["spec"]["ports"] = [
        {"name": name, "net": net, "direction": direction}
        for name, net, direction, role, xy, escape in rows
    ]
    args["layout"]["ports"] = {
        name: {"xy": xy, "escape": escape, **({"role": role} if role else {})}
        for name, net, direction, role, xy, escape in rows
    }
    args["preview_digest"] = preview_circuit(args["spec"], args["bindings"])["preview_digest"]


def role_sample():
    """Sample testbench with an extra power net; the load now consumes VDD!/EN."""
    args = sample_geometry()
    load = next(inst for inst in args["spec"]["instances"] if inst["id"] == "load")
    load["connections"] = {"P": "VDD!", "N": "EN"}
    args["spec"]["nets"] = args["spec"]["nets"] + [
        {"name": "VDD!", "scope": "global"},
        {"name": "EN", "scope": "local"},
    ]
    return args


def _codes(args):
    return {issue["code"] for issue in preview_geometry(**args)["plan"]["issues"]}


def test_power_bidirectional_input_ground_bands_pass_in_order():
    args = role_sample()
    _ports(
        args,
        [
            ("VDD!", "VDD!", "input", "power", [-6, 4], "right"),
            ("IN", "IN", "inputOutput", None, [-6, 2], "right"),
            ("EN", "EN", "input", None, [-6, 0], "right"),
            ("gnd!", "gnd!", "inputOutput", "ground", [-6, -2], "right"),
            ("OUT", "OUT", "output", None, [6, 0], "left"),
        ],
    )
    output = preview_geometry(**args)
    assert output["plan"]["geometry_checks_passed"], output["plan"]["issues"]
    roles = {port["name"]: port.get("role") for port in output["plan"]["ports"]}
    assert roles["VDD!"] == "power" and roles["gnd!"] == "ground"


def test_ground_above_inputs_is_rejected():
    args = role_sample()
    _ports(
        args,
        [
            ("VDD!", "VDD!", "input", "power", [-6, 2], "right"),
            ("gnd!", "gnd!", "inputOutput", "ground", [-6, 0], "right"),
            ("EN", "EN", "input", None, [-6, -2], "right"),
            ("OUT", "OUT", "output", None, [6, 0], "left"),
        ],
    )
    assert "port_role_band_order" in _codes(args)


def test_power_port_must_stay_left_and_above_signal_pins():
    args = role_sample()
    _ports(
        args,
        [
            ("VDD!", "VDD!", "input", "power", [-6, -2], "right"),
            ("EN", "EN", "input", None, [-6, 2], "right"),
            ("OUT", "OUT", "output", None, [6, 0], "left"),
        ],
    )
    assert "port_role_band_order" in _codes(args)
    right = role_sample()
    _ports(
        right,
        [
            ("VDD!", "VDD!", "input", "power", [6, 2], "left"),
            ("OUT", "OUT", "output", None, [6, 0], "left"),
        ],
    )
    assert "port_not_left" in _codes(right)


def test_direction_only_ports_keep_the_legacy_check():
    args = role_sample()
    _ports(
        args,
        [
            ("IN", "IN", "input", None, [-6, 2], "right"),
            ("OUT", "OUT", "output", None, [6, 0], "left"),
        ],
    )
    assert "port_role_band_order" not in _codes(args)
    reversed_bidir = role_sample()
    _ports(
        reversed_bidir,
        [
            ("IN", "IN", "inputOutput", None, [-6, 0], "right"),
            ("EN", "EN", "input", None, [-6, 2], "right"),
            ("OUT", "OUT", "output", None, [6, 0], "left"),
        ],
    )
    assert "input_output_pins_not_above_inputs" in _codes(reversed_bidir)
