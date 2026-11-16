"""Read-only netlist structure extraction and its CLI contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import parse_netlist


@pytest.mark.parametrize(
    ("name", "master", "kind"),
    [
        ("M1", "nch", "mos"),
        ("Yfoo", "pmos", "mos"),
        ("R1", "resistor", "resistor"),
        ("C1", "capacitor", "capacitor"),
        ("L1", "inductor", "inductor"),
        ("D1", "diode", "diode"),
        ("Q1", "npn", "bipolar"),
        ("V1", "vsource", "vsource"),
        ("I1", "isource", "isource"),
        ("X1", "leaf", "subckt"),
        ("Y1", "custom", "unknown"),
    ],
)
def test_device_classification_is_name_and_master_based(name, master, kind):
    assert parse_netlist.classify_device(name, master) == kind


@pytest.mark.parametrize(
    ("text", "dialect"),
    [
        (".subckt top a\n.ends\n", "spice"),
        ("subckt top a\nends\n", "spectre"),
        ("simulator lang=spectre\n", "spectre"),
        ("R1 a b 1k\n", "spice"),
    ],
)
def test_dialect_detection(text, dialect):
    assert parse_netlist.detect_dialect(text) == dialect


def test_spice_and_spectre_continuations_are_joined_with_start_lines():
    spice = "M1 d g s b nch\n+ w=1u l=0.5u\nR1 d x 1k\n+ tc=1\n"
    assert list(parse_netlist.logical_lines(spice, "spice")) == [
        (1, "M1 d g s b nch w=1u l=0.5u"),
        (3, "R1 d x 1k tc=1"),
    ]
    orphan = "+orphan\nR1 a b 1k\n"
    assert list(parse_netlist.logical_lines(orphan, "spice"))[0] == (1, "orphan")

    spectre = "M1 (d g s b) nch \\\n w=1u\nR1 (d x) resistor \\\\\n"
    assert list(parse_netlist.logical_lines(spectre, "spectre")) == [
        (1, "M1 (d g s b) nch w=1u"),
        (3, "R1 (d x) resistor \\\\"),
    ]


def test_comments_and_parameters_keep_positional_tokens_separate():
    assert parse_netlist.strip_comment("M1 a b nch $ note", "spice") == "M1 a b nch"
    assert parse_netlist.strip_comment("M1 a b nch // note", "spice") == "M1 a b nch"
    assert parse_netlist.strip_comment("M1 a b nch // note", "spectre") == "M1 a b nch"
    positional, params = parse_netlist.split_params(
        'a b W=1u l="2u" model=(nch pch)'
    )
    assert positional == ["a", "b"]
    assert params == {"w": "1u", "l": '"2u"', "model": "(nch pch)"}


def test_spice_element_shapes_cover_known_unknown_and_truncated_lines():
    mos = parse_netlist.parse_spice_element("M1 d g s b nch w=1u", 7)
    assert (mos.name, mos.master, mos.kind, mos.nodes, mos.params, mos.line) == (
        "M1", "nch", "mos", ("d", "g", "s", "b"), {"w": "1u"}, 7,
    )
    subckt = parse_netlist.parse_spice_element("X1 a b leaf", 2)
    assert (subckt.master, subckt.kind, subckt.nodes) == ("leaf", "subckt", ("a", "b"))
    unknown = parse_netlist.parse_spice_element("Y1 a b custom", 3)
    assert (unknown.master, unknown.kind, unknown.nodes) == ("custom", "unknown", ("a", "b"))
    truncated = parse_netlist.parse_spice_element("R1 a", 4)
    assert (truncated.master, truncated.kind, truncated.nodes) == ("R", "resistor", ("a",))
    assert parse_netlist.parse_spice_element("   ", 5) is None


def test_spectre_element_and_cell_derived_views():
    device = parse_netlist.parse_spectre_element("M1", "d g s b", "nch", "w=1u", 3)
    assert (device.kind, device.nodes, device.params) == (
        "mos", ("d", "g", "s", "b"), {"w": "1u"},
    )
    cell = parse_netlist.Cell(
        name="top",
        ports=["a", "b"],
        devices=[
            device,
            parse_netlist.Device("R1", "resistor", "resistor", ("a", "n"), {}, 4),
            parse_netlist.Device("X1", "leaf", "subckt", ("n", "b"), {}, 5),
            parse_netlist.Device("Y1", "custom", "unknown", ("n", "b"), {}, 6),
        ],
        line=1,
    )
    assert cell.internal_nets() == ["d", "g", "n", "s"]
    assert cell.summary() == {"mos": 1, "resistor": 1, "subckt": 1, "unknown": 1}
    assert cell.submasters() == ["custom", "leaf"]

    compact = parse_netlist.cell_payload(cell, include_devices=False)
    detailed = parse_netlist.cell_payload(cell, include_devices=True)
    assert compact["device_count"] == 4 and "devices" not in compact
    assert detailed["devices"][0]["nodes"] == ["d", "g", "s", "b"]


def test_parse_text_nested_spectre_cells_skip_directives_and_keep_outer_devices():
    text = """\
simulator lang=spectre
subckt top a b
M1 (a n b b) nch w=1u
subckt inner x y
R1 (x y) resistor r=1k
ends inner
X1 (n b) inner
ends top
"""
    dialect, cells = parse_netlist.parse_text(text)
    assert dialect == "spectre"
    assert [(cell.name, cell.line, cell.ports) for cell in cells] == [
        ("top", 2, ["a", "b"]),
        ("inner", 4, ["x", "y"]),
    ]
    assert [device.name for device in cells[0].devices] == ["M1", "X1"]
    assert [device.name for device in cells[1].devices] == ["R1"]
    assert cells[0].internal_nets() == ["n"]
    assert cells[0].submasters() == ["inner"]


def test_parse_text_spice_continuations_comments_and_outside_devices():
    text = """\
.subckt top a b
M1 a g b b nch w=1u
+ l=0.5u $ trailing comment
R1 a b 1k
.ends top
R2 a b 2k
"""
    dialect, cells = parse_netlist.parse_text(text, "spice")
    assert dialect == "spice"
    assert len(cells) == 1
    cell = cells[0]
    assert cell.ports == ["a", "b"]
    assert [device.name for device in cell.devices] == ["M1", "R1"]
    assert cell.devices[0].params == {"w": "1u", "l": "0.5u"}


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_main_json_filters_cells_and_honors_device_limit(tmp_path, capsys):
    path = _write(tmp_path / "deck.scs", """\
subckt top a b
M1 (a n b b) nch
R1 (a n) resistor
ends top
subckt other x y
R2 (x y) resistor
ends other
""")
    assert parse_netlist.main([str(path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"]["dialect"] == "spectre"
    assert payload["cell_count"] == 2
    assert payload["cells"][0]["devices"][0]["name"] == "M1"

    assert parse_netlist.main([str(path), "--cell", "TOP", "--max-devices", "0"]) == 0
    filtered = json.loads(capsys.readouterr().out)
    assert filtered["cell_count"] == 1
    assert "devices" not in filtered["cells"][0]

    assert parse_netlist.main([str(path), "--cell", "top", "--devices"]) == 0
    forced = json.loads(capsys.readouterr().out)
    assert len(forced["cells"][0]["devices"]) == 2


def test_main_text_output_and_error_paths(tmp_path, capsys):
    path = _write(tmp_path / "deck.sp", """\
.subckt top a b
R1 a b 1k
.ends
""")
    assert parse_netlist.main([str(path), "--format", "text"]) == 0
    out = capsys.readouterr().out
    assert "source:" in out and "cell top" in out and "resistor=1" in out

    assert parse_netlist.main([str(tmp_path / "missing")]) == 2
    assert "cannot read" in capsys.readouterr().err

    assert parse_netlist.main([str(path), "--cell", "absent"]) == 1
    assert "no matching cell" in capsys.readouterr().err
