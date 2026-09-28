from __future__ import annotations

from pathlib import Path

import pytest

from mtsnetlistor.errors import RequestValidationError
from mtsnetlistor.model import (
    CellNetlistSpec,
    ModelEntry,
    NetlistRequest,
    ProcessOptions,
    SimulatorOption,
    SourceDesign,
    TargetSelection,
    validate_oa_name,
)


def _source(tmp_path: Path) -> SourceDesign:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE work ./work\n", encoding="utf-8")
    return SourceDesign(cds, "work", "top", "schematic")


def test_oa_names_are_conservative() -> None:
    assert validate_oa_name("bufferx1", "cell") == "bufferx1"
    for value in ("", "../x", "a/b", "a-b", "a b", "-bad", "\n"):
        with pytest.raises(RequestValidationError):
            validate_oa_name(value)


def test_model_order_and_duplicate_file_section_are_validated(tmp_path: Path) -> None:
    source = _source(tmp_path)
    first = tmp_path / "first.lib"
    second = tmp_path / "second.lib"
    first.write_text("model", encoding="utf-8")
    second.write_text("model", encoding="utf-8")
    request = NetlistRequest(
        source,
        models=(ModelEntry(second, "tt"), ModelEntry(first, "ff")),
    ).validate()
    assert [item.file for item in request.models] == [second.resolve(), first.resolve()]
    with pytest.raises(RequestValidationError, match="duplicate model"):
        NetlistRequest(source, models=(ModelEntry(first, "tt"), ModelEntry(first, "tt"))).validate()


def test_model_disabled_duplicates_do_not_bypass_data_integrity(tmp_path: Path) -> None:
    source = _source(tmp_path)
    model = tmp_path / "model.lib"
    model.write_text("model", encoding="utf-8")
    with pytest.raises(RequestValidationError, match="duplicate model"):
        NetlistRequest(source, models=(ModelEntry(model, "tt", enabled=False), ModelEntry(model, "tt", enabled=False))).validate()


def test_options_and_process_values_are_typed_and_hspice_scope_is_locked(tmp_path: Path) -> None:
    source = _source(tmp_path)
    request = NetlistRequest(
        source,
        dialect="hspiceD",
        process_options=ProcessOptions(scale=0.9, temp=25),
        simulator_options=(SimulatorOption("parhier", "LOCAL"),),
    ).validate()
    assert request.process_options.scale == 0.9
    with pytest.raises(RequestValidationError, match="PARHIER"):
        NetlistRequest(source, dialect="hspiceD", simulator_options=(SimulatorOption("parhier", "GLOBAL"),)).validate()
    with pytest.raises(RequestValidationError, match="not qualified"):
        NetlistRequest(source, dialect="hspiceD", process_options=ProcessOptions(reltol=1e-3)).validate()
    with pytest.raises(RequestValidationError, match="not qualified"):
        NetlistRequest(source, dialect="hspiceD", simulator_options=(SimulatorOption("reltol", "1e-3", "real"),)).validate()
    with pytest.raises(RequestValidationError):
        NetlistRequest(source, process_options=ProcessOptions(scale=0)).validate()


def test_gmin_is_a_positive_process_option_and_preserves_validated_value(tmp_path: Path) -> None:
    source = _source(tmp_path)
    request = NetlistRequest(
        source,
        process_options=ProcessOptions(gmin="1.00e-12"),
    ).validate()
    assert request.process_options.gmin == "1.00e-12"
    with pytest.raises(RequestValidationError, match="gmin must be greater than zero"):
        NetlistRequest(source, process_options=ProcessOptions(gmin=0)).validate()
    hspice_request = NetlistRequest(
        source,
        dialect="hspiceD",
        process_options=ProcessOptions(gmin="1e-12"),
    ).validate()
    assert hspice_request.process_options.gmin == "1e-12"
    for invalid in ("1_000", "nan", "inf", "１e-12"):
        with pytest.raises(RequestValidationError, match="ASCII decimal"):
            NetlistRequest(
                source,
                process_options=ProcessOptions(gmin=invalid),
            ).validate()


def test_target_cell_defaults_to_source_and_view_mapping_is_dialect_specific(tmp_path: Path) -> None:
    request = NetlistRequest(_source(tmp_path), target=TargetSelection(library="work")).validate()
    assert request.target.cell == "top"
    assert request.output_suffix == ".spe"
    assert request.text_view == "spectreText"
    hspice = NetlistRequest(_source(tmp_path), dialect="hspiceD").validate()
    assert hspice.output_suffix == ".sp"
    assert hspice.text_view == "spiceText"


def test_overwrite_controls_are_independent_and_require_matching_generation_view(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    symbol_only = NetlistRequest(
        source,
        target=TargetSelection(
            library="work",
            generate_symbol_view=True,
            overwrite_symbol_view=True,
        ),
    ).validate()
    assert symbol_only.target.overwrite_symbol_view is True
    assert symbol_only.target.overwrite_netlist_view is False

    text_only = NetlistRequest(
        source,
        target=TargetSelection(
            library="work",
            generate_netlist_view=True,
            overwrite_netlist_view=True,
        ),
    ).validate()
    assert text_only.target.overwrite_symbol_view is False
    assert text_only.target.overwrite_netlist_view is True

    with pytest.raises(RequestValidationError, match="requires generate_symbol_view"):
        NetlistRequest(
            source,
            target=TargetSelection(
                library="work",
                overwrite_symbol_view=True,
            ),
        ).validate()


def test_multi_cell_specs_validate_independently_and_preserve_order(tmp_path: Path) -> None:
    source = _source(tmp_path)
    model = tmp_path / "model.lib"
    model.write_text("model", encoding="utf-8")
    request = NetlistRequest(
        source,
        cell_specs=(
            CellNetlistSpec("work", "second", "schematic"),
            CellNetlistSpec(
                "work",
                "first",
                "layout",
                models=(ModelEntry(model, "ss"),),
                process_options=ProcessOptions(temp=85),
                simulator_options=(SimulatorOption("reltol", "1e-3", "real"),),
            ),
        ),
    ).validate()
    assert [item.cell for item in request.selected_cells] == ["second", "first"]
    assert request.source.cell == "second"
    assert request.selected_cells[1].view == "layout"
    assert request.selected_cells[1].models[0].section == "ss"


def test_multi_cell_specs_reject_duplicate_design_identity(tmp_path: Path) -> None:
    source = _source(tmp_path)
    with pytest.raises(RequestValidationError, match="duplicate source cell"):
        NetlistRequest(
            source,
            cell_specs=(
                CellNetlistSpec("work", "a"),
                CellNetlistSpec("work", "a"),
            ),
        ).validate()
    with pytest.raises(RequestValidationError, match="requires generate_netlist_view"):
        NetlistRequest(
            source,
            target=TargetSelection(
                library="work",
                overwrite_netlist_view=True,
            ),
        ).validate()
