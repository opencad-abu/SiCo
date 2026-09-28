"""publication ownership cases regressions."""

from __future__ import annotations
from pathlib import Path
import pytest
from mtsnetlistor import publish, publication_text, publication_preflight
from mtsnetlistor.errors import MtsNetlistorError, RequestValidationError
from mtsnetlistor.model import NetlistRequest, TargetSelection
from publication_fixtures import (
    _setup,
    _managed_run_with_successful_artifacts,
)


def test_spectre_staging_adds_language_marker_without_rewriting_stable_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _target_cds, _source_lib, _target_lib, session, request = _setup(tmp_path)
    stable_before = (tmp_path / "bufferx1.spe").read_text(encoding="utf-8")
    # Simulate an older OCEAN/scoper artifact that has no language directive.
    (tmp_path / "bufferx1.spe").write_text(
        "subckt bufferx1 A VDD VSS Y\nends bufferx1\n", encoding="utf-8"
    )
    captured: dict[str, object] = {}

    def fake_import(import_request: object, *, environ: dict[str, str]) -> int:
        captured["request"] = import_request
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
    assert imported.source.suffix == ".scs"
    assert imported.source.read_text(encoding="utf-8").startswith(
        "simulator lang=spectre\nsubckt renamed A VDD VSS Y\n"
    )
    assert (tmp_path / "bufferx1.spe").read_text(encoding="utf-8") != stable_before
    assert (tmp_path / "bufferx1.spe").read_text(encoding="utf-8") == (
        "subckt bufferx1 A VDD VSS Y\nends bufferx1\n"
    )
    assert result.source_netlist == tmp_path / "run" / "publish" / "renamed.scs"


def test_failed_import_does_not_delete_artifacts_created_by_importer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _target_cds, _source_lib, target_lib, session, request = _setup(tmp_path)
    captured: dict[str, object] = {}

    def fake_import(import_request: object, *, environ: dict[str, str]) -> int:
        captured["request"] = import_request
        view_directory = target_lib / "renamed" / "spectreText"
        view_directory.mkdir(parents=True)
        (view_directory / "partial.netlist.oa").write_text("partial", encoding="utf-8")
        return 17

    monkeypatch.setattr(publication_text, "run_import", fake_import)
    monkeypatch.setattr(publication_preflight, "find_executable", lambda _: "/fake/cdsTextTo5x")
    monkeypatch.setattr(publication_text, "find_cds_lib_debug", lambda *_args, **_kwargs: None)
    with pytest.raises(MtsNetlistorError, match="exit code 17"):
        publish.publish_text_view(
            request,
            session,
            netlist=tmp_path / "bufferx1.spe",
            run_dir=tmp_path / "run",
        )
    assert (target_lib / "renamed" / "spectreText" / "partial.netlist.oa").read_text(
        encoding="utf-8"
    ) == "partial"


def test_strict_publication_accepts_only_manifested_success_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _target_cds, _source_lib, _target_lib, session, request = _setup(tmp_path)
    content = (
        "simulator lang=spectre\n"
        "subckt bufferx1 A VDD VSS Y\n"
        "ends bufferx1\n"
    )
    run, scoped, _stable = _managed_run_with_successful_artifacts(tmp_path, request, content)
    monkeypatch.setattr(publication_text, "run_import", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(publication_preflight, "find_executable", lambda _value: "/fake/cdsTextTo5x")
    monkeypatch.setattr(publication_text, "find_cds_lib_debug", lambda *_args, **_kwargs: None)

    result = publish.publish_text_view(
        request,
        session,
        netlist=scoped,
        run_dir=run,
        strict_ownership=True,
    )
    assert result.status == "succeeded"


def test_strict_publication_allows_target_selected_after_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _target_cds, _source_lib, _target_lib, session, publish_request = _setup(tmp_path)
    generation_request = NetlistRequest(
        publish_request.source,
        publish_request.dialect,
        publish_request.models,
        publish_request.process_options,
        publish_request.simulator_options,
        TargetSelection(),
    ).validate()
    content = (
        "simulator lang=spectre\n"
        "subckt bufferx1 A VDD VSS Y\n"
        "ends bufferx1\n"
    )
    run, scoped, _stable = _managed_run_with_successful_artifacts(
        tmp_path, generation_request, content
    )
    monkeypatch.setattr(publication_text, "run_import", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(publication_preflight, "find_executable", lambda _value: "/fake/cdsTextTo5x")
    monkeypatch.setattr(publication_text, "find_cds_lib_debug", lambda *_args, **_kwargs: None)

    result = publish.publish_text_view(
        publish_request,
        session,
        netlist=scoped,
        run_dir=run,
        strict_ownership=True,
    )
    assert result.status == "succeeded"
    assert result.target_library == "target"
    assert result.target_cell == "renamed"


def test_strict_publication_rejects_tampered_manifested_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _target_cds, _source_lib, _target_lib, session, request = _setup(tmp_path)
    content = (
        "simulator lang=spectre\n"
        "subckt bufferx1 A VDD VSS Y\n"
        "ends bufferx1\n"
    )
    run, scoped, _stable = _managed_run_with_successful_artifacts(tmp_path, request, content)
    scoped.write_text(content + "// tampered\n", encoding="utf-8")
    called = False

    def fake_import(*_args: object, **_kwargs: object) -> int:
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(publication_text, "run_import", fake_import)
    monkeypatch.setattr(publication_preflight, "find_executable", lambda _value: "/fake/cdsTextTo5x")
    with pytest.raises(RequestValidationError, match="digest mismatch"):
        publish.publish_text_view(
            request,
            session,
            netlist=scoped,
            run_dir=run,
            strict_ownership=True,
        )
    assert called is False


def test_public_validate_run_artifact_accepts_path_like_inputs(tmp_path: Path) -> None:
    source, _target_cds, _source_lib, _target_lib, _session_value, request = _setup(tmp_path)
    content = (
        "simulator lang=spectre\n"
        "subckt bufferx1 A VDD VSS Y\n"
        "ends bufferx1\n"
    )
    run, scoped, _stable = _managed_run_with_successful_artifacts(
        tmp_path, request, content
    )

    publish.validate_run_artifact(
        request,
        str(scoped),
        str(run),
        strict_ownership=True,
    )
