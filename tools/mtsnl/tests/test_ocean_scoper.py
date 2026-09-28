from __future__ import annotations

from pathlib import Path
import re

import pytest

from mtsnetlistor.model import ModelEntry, NetlistRequest, ProcessOptions, SimulatorOption, SourceDesign
from mtsnetlistor.ocean import raw_path_from_output, render_ocean_script
from mtsnetlistor.scoper.core import ScopeError, scope_netlist


def _request(tmp_path: Path, dialect: str = "spectre") -> NetlistRequest:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE work ./work\n", encoding="utf-8")
    model = tmp_path / "model.lib"
    model.write_text("model", encoding="utf-8")
    process_options = ProcessOptions(temp=27, scale=0.9, scalem=1, gmin="1.00e-12") if dialect == "spectre" else ProcessOptions(temp=27, scale=0.9)
    simulator_options = (SimulatorOption("reltol", "1e-3", "real"),) if dialect == "spectre" else ()
    return NetlistRequest(
        SourceDesign(cds, "work", "top", "schematic"),
        dialect=dialect,
        models=(ModelEntry(model, "tt"),),
        process_options=process_options,
        simulator_options=simulator_options,
    ).validate()


def test_spectre_ocean_uses_direct_create_netlist_and_no_run(tmp_path: Path) -> None:
    script = render_ocean_script(_request(tmp_path), workdir=tmp_path / "run")
    assert "simulator('spectre)" in script.text
    assert "createNetlist(?recreateAll t ?display nil)" in script.text
    assert "run()" not in script.text
    assert "modelFile" in script.text
    assert "('" in script.text and '"tt"' in script.text
    assert script.expected_filename == "input.scs"
    assert "'gmin 1.00e-12" in script.text


def test_hspice_ocean_uses_socket_final_netlist_and_no_run(tmp_path: Path) -> None:
    script = render_ocean_script(_request(tmp_path, "hspiceD"), workdir=tmp_path / "run")
    assert "simulator('hspiceD)" in script.text
    # IC23.10 rejects createFinalNetlist for this hspiceD integration
    # (ADE-3030); its qualified adapter emits the input through direct
    # createNetlist without ever invoking run().
    assert "createNetlist(?recreateAll t ?display nil)" in script.text
    assert "createFinalNetlist" not in script.text
    assert "run()" not in script.text
    assert script.expected_filename == "input.ckt"
    assert "netlistDir(\"" in script.text


def test_spectre_ocean_also_uses_private_netlist_directory(tmp_path: Path) -> None:
    script = render_ocean_script(_request(tmp_path), workdir=tmp_path / "run")
    assert 'netlistDir("' in script.text


def test_hspice_gmin_is_rendered_and_preserves_text(tmp_path: Path) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE work ./work\n", encoding="utf-8")
    request = NetlistRequest(
        SourceDesign(cds, "work", "top", "schematic"),
        dialect="hspiceD",
        process_options=ProcessOptions(scale=0.9, gmin="2.50e-12"),
    )
    script = render_ocean_script(request, workdir=tmp_path / "run")
    assert "option('scale 0.9 'gmin 2.50e-12)" in script.text


@pytest.mark.parametrize(
    "process_options",
    (
        ProcessOptions(tnom=27),
        ProcessOptions(scalem=1),
        ProcessOptions(reltol=1e-3),
    ),
)
def test_hspice_unqualified_process_options_are_rejected(
    tmp_path: Path, process_options: ProcessOptions
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE work ./work\n", encoding="utf-8")
    request = NetlistRequest(
        SourceDesign(cds, "work", "top", "schematic"),
        dialect="hspiceD",
        process_options=process_options,
    )
    with pytest.raises(ValueError, match="not qualified"):
        render_ocean_script(request, workdir=tmp_path / "run")


def test_hspice_gmin_advanced_option_is_reserved(tmp_path: Path) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE work ./work\n", encoding="utf-8")
    request = NetlistRequest(
        SourceDesign(cds, "work", "top", "schematic"),
        dialect="hspiceD",
        simulator_options=(SimulatorOption("gmin", "1e-12", "real"),),
    )
    with pytest.raises(ValueError, match="reserved"):
        request.validate()


def test_raw_path_sentinel_is_confined_to_run_root(tmp_path: Path) -> None:
    raw = tmp_path / "run" / "raw" / "input.scs"
    raw.parent.mkdir(parents=True)
    raw.write_text("subckt top a\nends top\n", encoding="utf-8")
    assert raw_path_from_output(f"MTS_NETLISTOR_RAW={raw}\n", run_root=tmp_path / "run") == raw.resolve()
    assert raw_path_from_output(f"\\o MTS_NETLISTOR_RAW={raw}\n", run_root=tmp_path / "run") == raw.resolve()
    with pytest.raises(ValueError, match="escaped"):
        raw_path_from_output(f"MTS_NETLISTOR_RAW={tmp_path / 'outside'}\n", run_root=tmp_path / "run")


def test_scope_rejects_missing_or_duplicate_top() -> None:
    with pytest.raises(ScopeError, match="exactly one"):
        scope_netlist("subckt a x\nends a\n", "spectre", "top")
    with pytest.raises(ScopeError, match="exactly one"):
        scope_netlist("subckt top x\nends top\nsubckt top y\nends top\n", "spectre", "top")


def _golden_netlist(dialect: str, name: str) -> Path:
    base = Path("/workarea/xh/smic28/simulation")
    directory = base / ("bufferx1_spectre" if dialect == "spectre" else "bufferx1_hspiceD")
    path = directory / name
    if not path.is_file():
        pytest.skip(f"Cadence golden fixture is not available: {path}")
    return path


@pytest.mark.parametrize(
    ("dialect", "raw_name", "scoped_name", "top_header", "dep_header"),
    (
        ("spectre", "input.scs", "bufferx1.spe", "subckt bufferx1 A VDD VSS Y", "subckt inv A VDD VSS Y"),
        ("hspiceD", "input.ckt", "bufferx1.sp", ".subckt bufferx1 a vdd vss y", ".subckt inv a vdd vss y"),
    ),
)
def test_cadence_golden_scope_and_idempotence(
    dialect: str,
    raw_name: str,
    scoped_name: str,
    top_header: str,
    dep_header: str,
) -> None:
    raw = _golden_netlist(dialect, raw_name).read_text(encoding="utf-8", errors="replace")
    expected = _golden_netlist(dialect, scoped_name).read_text(encoding="utf-8", errors="replace")

    result = scope_netlist(raw, dialect, "bufferx1")
    assert result.top == "bufferx1"
    assert result.dependencies == ("inv",)
    assert result.ports == (("A", "VDD", "VSS", "Y") if dialect == "spectre" else ("a", "vdd", "vss", "y"))
    assert re.search(rf"(?im)^{re.escape(top_header)}\s*$", result.output)
    assert re.search(rf"(?im)^{re.escape(dep_header)}\s*$", result.output)
    assert result.output.count("subckt inv" if dialect == "spectre" else ".subckt inv") == 1
    assert result.output.endswith("\n")

    # The checked-in Cadence result is already a scoped deck.  It is a useful
    # byte-stability oracle even though Cadence's comment/include ordering is
    # intentionally not reproduced byte-for-byte by this renderer.
    expected_result = scope_netlist(expected, dialect, "bufferx1")
    assert expected_result.dependencies == result.dependencies
    assert expected_result.ports == result.ports
    assert expected_result.output.count("subckt inv" if dialect == "spectre" else ".subckt inv") == 1
    assert scope_netlist(result.output, dialect, "bufferx1").output == result.output

    if dialect == "spectre":
        assert re.search(r"(?im)^simulator\s+lang\s*=\s*spectre\s*$", result.output)
        assert "simulatorOptions options" in result.output
        assert 'include "ade_e.scs"' not in result.output
        assert "sensfile=" not in result.output
        assert "checklimitdest=" not in result.output
        assert "modelParameter info" not in result.output
        assert "global 0" not in result.output
    else:
        assert "PARHIER=LOCAL" in result.output.upper()
        assert result.output.upper().count(".END\n") == 1


def test_spectre_scoped_output_adds_language_directive_when_raw_deck_omits_it() -> None:
    raw = """// generated without language marker
subckt top (a)
  R0 (a 0) resistor r=1k
ends top
"""
    result = scope_netlist(raw, "spectre", "top")
    assert result.output.startswith("simulator lang=spectre\n")
    assert scope_netlist(result.output, "spectre", "top").output == result.output


def test_spectre_and_hspice_continuations_remain_logical_and_ordered() -> None:
    spectre = """simulator lang=spectre
include \"models\\\\path.lib\" \\
    section=tt
subckt leaf (n)
ends leaf
subckt top (n)
  X0 (n) leaf
ends top
"""
    spectre_result = scope_netlist(spectre, "spectre", "top")
    assert spectre_result.dependencies == ("leaf",)
    assert 'include "models\\\\path.lib" \\' in spectre_result.output
    assert "    section=tt" in spectre_result.output

    hspice = """** header
.OPTION
+    SCALE=0.9
+    PARHIER=LOCAL
.subckt leaf n
.ends leaf
.subckt top n
xi0 n leaf
.ends top
.END
"""
    hspice_result = scope_netlist(hspice, "hspiceD", "top")
    assert hspice_result.dependencies == ("leaf",)
    assert ".OPTION\n+    SCALE=0.9\n+    PARHIER=LOCAL" in hspice_result.output
    assert hspice_result.output.upper().count(".END\n") == 1


def test_spectre_drops_ade_runtime_include_and_result_options() -> None:
    raw = r'''// Generated for: spectre
simulator lang=spectre
include "ade_e.scs"
include "/models/tt.lib" section=tt
subckt top (a)
  R0 (a 0) resistor r=1k
ends top
simulatorOptions options reltol=1e-3 temp=27 \
    sensfile="../../../../psf/sens.output" checklimitdest=psf \
    scale=0.9
saveOptions options save=allpub
'''

    result = scope_netlist(raw, "spectre", "top")

    assert 'include "ade_e.scs"' not in result.output
    assert 'include "/models/tt.lib" section=tt' in result.output
    assert "reltol=1e-3" in result.output
    assert "temp=27" in result.output
    assert "scale=0.9" in result.output
    assert "sensfile" not in result.output
    assert "checklimitdest" not in result.output
    assert "saveOptions" not in result.output
    assert any("runtime include" in item for item in result.dropped)
    assert any("runtime simulator option sensfile" in item for item in result.dropped)
    assert any("runtime simulator option checklimitdest" in item for item in result.dropped)
    assert any("saveOptions" in item for item in result.dropped)
    assert scope_netlist(result.output, "spectre", "top").output == result.output


def test_spectre_removes_options_statement_when_only_runtime_fields_remain() -> None:
    raw = '''
subckt top (a)
ends top
simulatorOptions options sensfile="psf/sens.output" checklimitdest=psf
'''

    result = scope_netlist(raw, "spectre", "top")

    assert "simulatorOptions" not in result.output
    assert "sensfile" not in result.output
    assert "checklimitdest" not in result.output
    assert len([item for item in result.dropped if "runtime simulator option" in item]) == 2


def test_spectre_runtime_option_matching_is_case_insensitive_and_preserves_similar_names() -> None:
    raw = '''
subckt top (a)
ends top
simulatorOptions options SENSFILE="psf/sens.output" my_sensfile="keep" checklimitdest=PSF
'''

    result = scope_netlist(raw, "spectre", "top")

    assert "SENSFILE" not in result.output
    assert "checklimitdest" not in result.output
    assert 'my_sensfile="keep"' in result.output


@pytest.mark.parametrize(
    ("dialect", "text", "message"),
    (
        ("spectre", "ends top\nsubckt top (a)\nends top\n", "unmatched"),
        ("spectre", "subckt top (a)\nends other\n", "mismatched"),
        ("spectre", "subckt top (a)\n", "unbalanced"),
        ("hspiceD", ".ends top\n.subckt top a\n.ends top\n", "unmatched"),
        ("hspiceD", ".subckt top a\n.ends other\n", "mismatched"),
        ("hspiceD", ".subckt top a\n", "unbalanced"),
    ),
)
def test_scope_rejects_unbalanced_or_mismatched_subckt_structure(
    dialect: str, text: str, message: str
) -> None:
    with pytest.raises(ScopeError, match=message):
        scope_netlist(text, dialect, "top")


@pytest.mark.parametrize(
    ("dialect", "text"),
    (
        ("spectre", "subckt top (a)\nends top\nsubckt top (b)\nends top\n"),
        # HSPICE lookup is case-insensitive, therefore Top and top collide.
        ("hspiceD", ".subckt Top a\n.ends Top\n.subckt top b\n.ends top\n"),
    ),
)
def test_scope_rejects_duplicate_subckt_definitions(dialect: str, text: str) -> None:
    with pytest.raises(ScopeError, match="duplicate"):
        scope_netlist(text, dialect, "top")


def test_nested_and_reachable_subckt_definitions_are_closed_in_source_order() -> None:
    text = """// source header
subckt top (a)
  X0 (a) mid
  subckt mid (n)
    X1 (n) leaf
  ends mid
  subckt leaf (n)
  ends leaf
  subckt unused (n)
  ends unused
ends top
"""
    result = scope_netlist(text, "spectre", "top")
    assert result.dependencies == ("mid", "leaf")
    assert "subckt unused" not in result.output
    assert result.output.index("subckt mid") < result.output.index("subckt leaf")
    assert scope_netlist(result.output, "spectre", "top").output == result.output


@pytest.mark.parametrize(
    ("dialect", "text", "needle"),
    (
        ("spectre", "mystery directive\nsubckt top (a)\n  X0 (a) primitive\nends top\n", "unknown Spectre statement"),
        ("hspiceD", "bogus directive\n.subckt top a\nxi0 a primitive\n.ends top\n", "unknown HSPICE statement"),
    ),
)
def test_unknown_statement_is_retained_and_reported(dialect: str, text: str, needle: str) -> None:
    result = scope_netlist(text, dialect, "top")
    assert any(needle in warning for warning in result.warnings)
    assert ("mystery directive" if dialect == "spectre" else "bogus directive") in result.output
    assert result.report()["warnings"] == list(result.warnings)
