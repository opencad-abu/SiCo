"""config serialization cases regressions."""

from __future__ import annotations
from pathlib import Path
import pytest
from mtsnetlistor.config import (
    canonical_request_digest,
    load_request,
    request_to_dict,
    save_request,
)
from mtsnetlistor.model import (
    CellNetlistSpec,
    ModelEntry,
    NetlistRequest,
    ProcessOptions,
    SimulatorOption,
    SourceDesign,
    TargetSelection,
)
from config_fixtures import (
    _write_request,
)


def test_saved_multi_cell_configuration_is_loadable_toml(tmp_path: Path) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE work ./work\n", encoding="utf-8")
    (tmp_path / "work").mkdir()
    request = NetlistRequest(
        SourceDesign(cds, "work", "first"),
        cell_specs=(
            CellNetlistSpec(
                "work",
                "first",
                process_options=ProcessOptions(temp=27, gmin="1.00e-12"),
                dialect="spectre",
                target=TargetSelection(library="work", generate_netlist_view=True),
            ),
            CellNetlistSpec(
                "work",
                "second",
                process_options=ProcessOptions(temp=85),
                dialect="hspiceD",
                target=TargetSelection(library="work", generate_symbol_view=True),
            ),
        ),
    ).validate()
    loaded = load_request(save_request(request, tmp_path / "saved.toml"))
    assert request_to_dict(loaded) == request_to_dict(request)
    assert [cell.cell for cell in loaded.cell_specs] == ["first", "second"]
    assert [cell.dialect for cell in loaded.cell_specs] == ["spectre", "hspiceD"]
    assert loaded.cell_specs[0].target.generate_netlist_view
    assert loaded.cell_specs[1].target.generate_symbol_view
    assert [cell.process_options.gmin for cell in loaded.cell_specs] == [
        "1.00e-12",
        None,
    ]


def test_saved_multi_cell_configuration_preserves_all_ordered_per_cell_state(
    tmp_path: Path,
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE work ./work\n", encoding="utf-8")
    (tmp_path / "work").mkdir()
    startup = tmp_path / "startup.il"
    startup.write_text("t\n", encoding="utf-8")
    simrc = tmp_path / ".simrc"
    simrc.write_text("simrc\n", encoding="utf-8")
    model_a = tmp_path / "a.lib"
    model_b = tmp_path / "b.lib"
    model_a.write_text("model a\n", encoding="utf-8")
    model_b.write_text("model b\n", encoding="utf-8")

    request = NetlistRequest(
        SourceDesign(cds, "work", "first", "schematic", startup, simrc),
        cell_specs=(
            CellNetlistSpec(
                "work",
                "first",
                "schematic",
                models=(
                    ModelEntry(model_b, "ff", "second model", False),
                    ModelEntry(model_a, "tt", "first model", True),
                ),
                process_options=ProcessOptions(temp=27, scale=0.9),
                simulator_options=(
                    SimulatorOption("method", "gear2", "enum", True, ("trap", "gear2")),
                    SimulatorOption("reltol", "1e-3", "real", False),
                ),
                dialect="spectre",
                target=TargetSelection(
                    library="targetA",
                    cell="first_mts",
                    generate_symbol_view=True,
                    overwrite_symbol_view=True,
                ),
            ),
            CellNetlistSpec(
                "work",
                "second",
                "layout",
                models=(ModelEntry(model_a, "ss", "layout model"),),
                process_options=ProcessOptions(temp=85, scale=1.1),
                simulator_options=(
                    SimulatorOption("save", "all", "string"),
                    SimulatorOption("maxnotes", "10", "integer"),
                ),
                dialect="spectre",
                target=TargetSelection(
                    library="targetB",
                    cell="second_mts",
                    generate_netlist_view=True,
                    overwrite_netlist_view=True,
                ),
            ),
        ),
    ).validate()

    destination = save_request(request, tmp_path / "nested" / "saved.toml")
    loaded = load_request(destination)

    assert request_to_dict(loaded) == request_to_dict(request)
    assert canonical_request_digest(loaded) == canonical_request_digest(request)
    assert loaded.source.startup_file == startup.resolve()
    assert loaded.source.simrc == simrc.resolve()
    assert [spec.cell for spec in loaded.cell_specs] == ["first", "second"]
    assert [item.file for item in loaded.cell_specs[0].models] == [
        model_b.resolve(),
        model_a.resolve(),
    ]
    assert [item.name for item in loaded.cell_specs[0].simulator_options] == [
        "method",
        "reltol",
    ]
    assert loaded.cell_specs[0].target == TargetSelection(
        library="targetA",
        cell="first_mts",
        generate_symbol_view=True,
        overwrite_symbol_view=True,
    )
    assert loaded.cell_specs[1].target == TargetSelection(
        library="targetB",
        cell="second_mts",
        generate_netlist_view=True,
        overwrite_netlist_view=True,
    )


def test_save_request_keeps_existing_configuration_when_atomic_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mtsnetlistor import artifacts

    request_path, _ = _write_request(tmp_path)
    request = load_request(request_path)
    destination = tmp_path / "saved.toml"
    destination.write_text("previous configuration\n", encoding="utf-8")

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(artifacts.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected replace failure"):
        save_request(request, destination)

    assert destination.read_text(encoding="utf-8") == "previous configuration\n"
    assert list(tmp_path.glob(".saved.toml.*.tmp")) == []
