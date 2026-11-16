"""RNM port, specification and modeling-style conformance checks."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "schematic-to-rnm" / "scripts"))

from rnm_conformance import (
    REAL_NET_TYPES,
    check_modeling_style,
    check_ports,
    check_spec,
)
from rnm_findings import Finding
from rnm_module import ModuleFacts


def module(**changes):
    value = ModuleFacts(
        name="top",
        header_ports=["VDD", "IN", "OUT"],
        directions={"VDD": "input", "IN": "input", "OUT": "output"},
        net_types={"VDD": "wreal", "IN": "logic", "OUT": "wreal"},
        parameters=["gain"],
        ansi_header=True,
    )
    for key, item in changes.items():
        setattr(value, key, item)
    return value


def codes(findings):
    return [finding.category for finding in findings]


def test_ports_detect_missing_extra_order_and_direction():
    findings = check_ports(module(), ["VDD", "IN", "OUT"])
    assert findings == []
    findings = check_ports(module(), ["VDD", "MISSING", "OUT"])
    assert codes(findings) == ["port-missing", "port-extra"]
    findings = check_ports(module(), ["IN", "VDD", "OUT"])
    assert codes(findings) == ["port-order"]
    findings = check_ports(module(directions={"VDD": "input"}), ["VDD", "IN", "OUT"])
    assert "port-direction" in codes(findings)


def test_spec_checks_cell_direction_net_type_and_metric_exposure():
    findings = check_spec(module(), {
        "cell": "other",
        "ports": {
            "VDD": {"direction": "output", "kind": "power"},
            "IN": {"kind": "analog"},
            "OUT": {"kind": "digital"},
        },
        "metrics": ["gain", "missing"],
    })
    assert codes(findings) == [
        "spec-cell", "spec-direction", "net-type", "net-type", "spec-metric",
    ]
    assert findings[3].message == "digital port is declared wreal; expected logic/wire"
    assert findings[4].subject == "missing"


def test_spec_accepts_real_power_and_analog_ports():
    findings = check_spec(module(net_types={"VDD": "wreal", "IN": "wreal",
                                             "OUT": "wreal"}), {
        "ports": {"VDD": {"kind": "power"}, "IN": {"kind": "analog"}},
    })
    assert findings == []


def test_modeling_style_requires_real_ports_import_behavior_and_timescale():
    text = "module top; endmodule"
    findings = check_modeling_style(module(net_types={"VDD": "wreal", "IN": "logic",
                                                       "OUT": "wreal"}), text)
    assert "style" in codes(findings)
    assert any("timescale" in finding.message for finding in findings)
    assert any("behavior" in finding.message for finding in findings)

    broken = """\
`timescale 1ns/1ps
module top(input wreal IN, output wreal OUT);
  wreal4state VDD;
  nmos (OUT, IN, VDD);
  assign OUT = IN;
endmodule
"""
    findings = check_modeling_style(module(net_types={"IN": "wreal", "OUT": "wreal"}), broken)
    assert "style" in codes(findings)
    assert any("cds_rnm_pkg" in finding.message for finding in findings)
    assert any("switch primitives" in finding.message for finding in findings)


def test_modeling_style_accepts_behavior_and_detects_undriven_output():
    text = """\
`timescale 1ns/1ps
module top(input wreal IN, output logic OUT);
  assign OUT = IN;
endmodule
"""
    assert check_modeling_style(module(), text) == []
    undriven = check_modeling_style(module(), """\
`timescale 1ns/1ps
module top(input wreal IN, output wreal OUT);
endmodule
""")
    assert "undriven" in codes(undriven)


def test_real_net_type_set_is_explicit():
    assert "wreal" in REAL_NET_TYPES and "logic" not in REAL_NET_TYPES
