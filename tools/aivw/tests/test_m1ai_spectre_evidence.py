from __future__ import annotations

from pathlib import Path

import pytest

from aivworkbench.errors import EnvironmentError
from aivworkbench.m1ai_spectre_evidence import (
    extract_comparator_subckts,
    parse_spectre_evidence_log,
    render_ocean_script,
    render_spectre_deck,
)


SOURCE = r"""
// Library name: saradc
// Cell name: comparator_new
// View name: schematic
subckt comparator_new Din\+ Din\- clk out outb inh_groundvssa! inh_power
    I0 (Din\- Din\+ net_13 net5 net4) latch_buffer
ends comparator_new
// End of subcircuit definition.

// Library name: saradc
// Cell name: comparator_offset_TB_new
// View name: schematic
I0 (a b c d e f g) comparator_new
"""


def test_extract_comparator_subckts_excludes_ade_testbench() -> None:
    fragment = extract_comparator_subckts(SOURCE)
    assert "subckt comparator_new" in fragment
    assert "comparator_offset_TB_new" not in fragment
    assert "I0 (a b c d e f g)" not in fragment


def test_render_deck_uses_real_seven_port_dut_and_matched_vectors(tmp_path: Path) -> None:
    deck = render_spectre_deck(extract_comparator_subckts(SOURCE), tmp_path / "model.scs")
    assert "IPOS (dinp_pos dinm_pos clk out_pos outb_pos vssa! vdda!) comparator_new" in deck
    assert "VPP (dinp_pos 0) vsource type=dc dc=0.7" in deck
    assert "VNP (dinp_neg 0) vsource type=dc dc=0.5" in deck
    assert "VZP (dinp_zero 0) vsource type=dc dc=0.6" in deck
    assert "tran tran stop=3n" in deck


def test_render_ocean_script_samples_all_outputs(tmp_path: Path) -> None:
    script = render_ocean_script(tmp_path / "psf")
    assert 'value(v("out_pos")' in script
    assert 'value(v("outb_zero")' in script
    assert "AIVW_SPECTRE_EVIDENCE checks=3 PASS" in script


def test_parse_spectre_evidence_log_classifies_measured_outputs() -> None:
    text = "\n".join(
        (
            "AIVW_SPECTRE_EVIDENCE case=nominal-positive out=1.19 outb=0.01",
            "AIVW_SPECTRE_EVIDENCE case=nominal-negative out=0.02 outb=1.18",
            "AIVW_SPECTRE_EVIDENCE case=nominal-zero out=0.59 outb=0.595",
        )
    )
    cases = parse_spectre_evidence_log(text)
    assert cases["nominal-positive"]["measurements"]["decision"] == "positive"
    assert cases["nominal-negative"]["measurements"]["decision"] == "negative"
    assert cases["nominal-zero"]["measurements"]["decision"] == "tie"


def test_parse_spectre_evidence_log_rejects_non_finite_measurement() -> None:
    text = "\n".join(
        (
            "AIVW_SPECTRE_EVIDENCE case=nominal-positive out=nan outb=0",
            "AIVW_SPECTRE_EVIDENCE case=nominal-negative out=0 outb=1.2",
            "AIVW_SPECTRE_EVIDENCE case=nominal-zero out=0.6 outb=0.6",
        )
    )
    with pytest.raises(EnvironmentError, match="non-finite"):
        parse_spectre_evidence_log(text)
