"""publication text cases regressions."""

from __future__ import annotations
from pathlib import Path
import pytest
from mtsnetlistor import publish, publication_text, publication_preflight, publication_netlist
from mtsnetlistor.model import NetlistRequest, TargetSelection
from publication_fixtures import (
    _request,
    _setup,
)


def test_rename_top_netlist_changes_only_top_declaration_and_terminator() -> None:
    raw = (
        "subckt bufferx1 A VDD VSS Y\n"
        "  x1 (...) inv\n"
        "ends bufferx1\n"
        "subckt bufferx1_nested A Y\n"
        "ends bufferx1_nested\n"
    )
    renamed = publish.rename_top_netlist(raw, "spectre", "bufferx1", "top_copy")
    assert renamed == (
        "subckt top_copy A VDD VSS Y\n"
        "  x1 (...) inv\n"
        "ends top_copy\n"
        "subckt bufferx1_nested A Y\n"
        "ends bufferx1_nested\n"
    )


def test_spectre_language_marker_helper_is_idempotent() -> None:
    raw = "subckt top A\nends top\n"
    marked = publication_netlist._ensure_spectre_language(raw)
    assert marked == "simulator lang=spectre\n" + raw
    assert publication_netlist._ensure_spectre_language(marked) == marked
    # Do not mistake a different simulator language for a Spectre marker.
    spice = "simulator lang=spice\n" + raw
    assert publication_netlist._ensure_spectre_language(spice).startswith(
        "simulator lang=spectre\nsimulator lang=spice\n"
    )


def test_target_overlay_and_environment_do_not_leak_source_selector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, target_cds, _source_lib, _target_lib, session, request = _setup(tmp_path)
    captured: dict[str, object] = {}

    def fake_import(import_request: object, *, environ: dict[str, str]) -> int:
        captured["request"] = import_request
        captured["environment"] = dict(environ)
        return 0

    monkeypatch.setattr(publication_text, "run_import", fake_import)
    monkeypatch.setattr(publication_preflight, "find_executable", lambda _: "/fake/cdsTextTo5x")
    monkeypatch.setattr(publication_text, "find_cds_lib_debug", lambda *_args, **_kwargs: None)
    monkeypatch.setenv("CDS_LIB", str(source))
    monkeypatch.setenv("CDS_CDSLIB", str(source))
    monkeypatch.setenv("CDS_MPS_SESSION", "virtuoso405942")
    monkeypatch.setenv("CDS_MPS_HOST", "work-srv")
    monkeypatch.setenv("CDS_MPS_FUTURE_SELECTOR", "must-not-leak")
    monkeypatch.setenv("MTS_NETLISTOR_TARGET_CDSLIB", str(target_cds))
    result = publish.publish_text_view(
        request,
        session,
        netlist=tmp_path / "bufferx1.spe",
        run_dir=tmp_path / "run",
    )

    overlay = Path(result.target_overlay or "")
    assert overlay.read_text(encoding="utf-8") == f"INCLUDE {target_cds.resolve()}\n"
    environment = captured["environment"]
    assert isinstance(environment, dict)
    assert environment["CDS_LIB"] == str(overlay)
    assert environment["CDS_CDSLIB"] == str(overlay)
    assert environment["MTS_NETLISTOR_CDSLIB"] == str(overlay)
    assert "MTS_NETLISTOR_TARGET_CDSLIB" not in environment
    assert not any(name.startswith("CDS_MPS_") for name in environment)
    assert all(str(source.resolve()) not in value for value in environment.values())


def test_text_overwrite_is_allowed_only_with_text_checkbox_and_reports_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _target_cds, _source_lib, target_lib, session, request = _setup(tmp_path)
    view_directory = target_lib / "renamed" / "spectreText"
    view_directory.mkdir(parents=True)
    (view_directory / "existing.marker").write_text("keep", encoding="utf-8")
    overwrite_request = NetlistRequest(
        request.source,
        request.dialect,
        request.models,
        request.process_options,
        request.simulator_options,
        TargetSelection(
            "target",
            "renamed",
            False,
            True,
            overwrite_netlist_view=True,
        ),
    ).validate()
    monkeypatch.setattr(publication_text, "run_import", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(publication_preflight, "find_executable", lambda _: "/fake/cdsTextTo5x")
    monkeypatch.setattr(publication_text, "find_cds_lib_debug", lambda *_args, **_kwargs: None)

    result = publish.publish_text_view(
        overwrite_request,
        session,
        netlist=tmp_path / "bufferx1.spe",
        run_dir=tmp_path / "run",
    )

    assert result.created_view is False
    assert result.replaced_view is True


def test_successful_fake_import_receives_renamed_request_and_target_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, target_cds, _source_lib, target_lib, session, request = _setup(tmp_path)
    captured: dict[str, object] = {}

    def fake_import(import_request: object, *, environ: dict[str, str]) -> int:
        captured["request"] = import_request
        captured["environment"] = environ
        return 0

    monkeypatch.setattr(publication_text, "run_import", fake_import)
    monkeypatch.setattr(publication_preflight, "find_executable", lambda _: "/fake/cdsTextTo5x")
    monkeypatch.setattr(publication_text, "find_cds_lib_debug", lambda *_args, **_kwargs: None)
    result = publish.publish_text_view(
        request,
        session,
        netlist=tmp_path / "bufferx1.spe",
        run_dir=tmp_path / "run",
    )

    imported = captured["request"]
    assert isinstance(imported, publication_text.ImportRequest)
    assert imported.library == "target"
    assert imported.cell == "renamed"
    assert imported.view == "spectreText"
    assert imported.netlist_format == "spectre"
    assert imported.source.suffix == ".scs"
    assert imported.cds_library_file == Path(result.target_overlay or "")
    assert imported.expected_library_path == target_lib.resolve()
    assert imported.source.read_text(encoding="utf-8").startswith(
        "simulator lang=spectre\nsubckt renamed A VDD VSS Y\n"
    )
    assert "ends renamed\n" in imported.source.read_text(encoding="utf-8")
    assert str(target_cds.resolve()) in imported.cds_library_file.read_text(encoding="utf-8")
    assert result.created_view is True


def test_same_cell_spectre_publish_stages_marker_even_for_scs_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_cds, target_cds, _source_lib, target_lib, session, request = _setup(tmp_path)
    same_cell_request = _request(source_cds, target_cell="bufferx1")
    source = tmp_path / "bufferx1.scs"
    source.write_text(
        "// legacy deck without a language marker\n"
        "subckt bufferx1 A VDD VSS Y\n"
        "ends bufferx1\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_import(import_request: object, *, environ: dict[str, str]) -> int:
        captured["request"] = import_request
        return 0

    monkeypatch.setattr(publication_text, "run_import", fake_import)
    monkeypatch.setattr(publication_preflight, "find_executable", lambda _: "/fake/cdsTextTo5x")
    monkeypatch.setattr(publication_text, "find_cds_lib_debug", lambda *_args, **_kwargs: None)
    result = publish.publish_text_view(
        same_cell_request,
        session,
        netlist=source,
        run_dir=tmp_path / "run",
    )

    imported = captured["request"]
    assert isinstance(imported, publication_text.ImportRequest)
    assert imported.source.suffix == ".scs"
    assert imported.source != source
    assert imported.source.read_text(encoding="utf-8").startswith(
        "simulator lang=spectre\n// legacy deck without a language marker\n"
    )
    assert source.read_text(encoding="utf-8").startswith("// legacy deck")
    assert result.target_library == "target"
    assert result.target_cell == "bufferx1"
