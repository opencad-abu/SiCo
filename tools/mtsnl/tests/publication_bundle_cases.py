"""publication bundle cases regressions."""

from __future__ import annotations
from pathlib import Path
import pytest
from mtsnetlistor import publish, publication_preflight
from mtsnetlistor.errors import MtsNetlistorError, RequestValidationError
from mtsnetlistor.model import NetlistRequest, TargetSelection
from publication_fixtures import (
    _setup,
)


def test_bundle_preflight_rejects_either_existing_view_before_symbol_or_text_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _target_cds, _source_lib, target_lib, session, request = _setup(tmp_path)
    # Enable both optional publications for the same immutable request.
    request = NetlistRequest(
        request.source,
        request.dialect,
        request.models,
        request.process_options,
        request.simulator_options,
        TargetSelection("target", "renamed", True, True),
    ).validate()
    view_directory = target_lib / "renamed" / "spectreText"
    view_directory.mkdir(parents=True)
    (view_directory / "existing.marker").write_text("keep", encoding="utf-8")
    called = {"symbol": False, "text": False}

    def fake_symbol(*_args: object, **_kwargs: object) -> object:
        called["symbol"] = True
        raise AssertionError("symbol stage must not start after preflight conflict")

    def fake_text(*_args: object, **_kwargs: object) -> object:
        called["text"] = True
        raise AssertionError("text stage must not start after preflight conflict")

    monkeypatch.setattr(publish, "transfer_symbol", fake_symbol)
    monkeypatch.setattr(publish, "publish_text_view", fake_text)
    with pytest.raises(RequestValidationError, match="already exists"):
        publish.publish_bundle(
            request,
            session,
            netlist=tmp_path / "bufferx1.spe",
            run_dir=tmp_path / "run",
        )
    assert called == {"symbol": False, "text": False}
    assert (view_directory / "existing.marker").read_text(encoding="utf-8") == "keep"


def test_bundle_reports_manual_cleanup_when_symbol_succeeded_before_text_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _target_cds, _source_lib, _target_lib, session, request = _setup(tmp_path)
    request = NetlistRequest(
        request.source,
        request.dialect,
        request.models,
        request.process_options,
        request.simulator_options,
        TargetSelection("target", "renamed", True, True),
    ).validate()
    source_result = object()

    monkeypatch.setattr(publication_preflight, "find_executable", lambda _value: "/fake/cdsTextTo5x")
    monkeypatch.setattr(publish, "transfer_symbol", lambda *_args, **_kwargs: source_result)
    monkeypatch.setattr(
        publish,
        "publish_text_view",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(MtsNetlistorError("injected text failure")),
    )
    result = publish.publish_bundle(
        request,
        session,
        netlist=tmp_path / "bufferx1.spe",
        run_dir=tmp_path / "run",
    )
    assert result.status == "manual_cleanup_required"
    assert "injected text failure" in result.message
    assert "automatic OA rollback is not qualified" in result.message
    assert result.symbol is source_result
    assert result.text is None
    assert result.affected_views == (
        str(tmp_path / "target-lib" / "renamed" / "symbol"),
        str(tmp_path / "target-lib" / "renamed" / "spectreText"),
    )
    assert result.rollback == ()


def test_bundle_reports_manual_cleanup_for_partial_text_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _target_cds, _source_lib, target_lib, session, request = _setup(tmp_path)
    request = NetlistRequest(
        request.source,
        request.dialect,
        request.models,
        request.process_options,
        request.simulator_options,
        TargetSelection("target", "renamed", False, True),
    ).validate()
    view = target_lib / "renamed" / "spectreText"

    monkeypatch.setattr(publication_preflight, "find_executable", lambda _value: "/fake/cdsTextTo5x")

    def failing_text(*_args: object, **_kwargs: object) -> object:
        view.mkdir(parents=True)
        (view / "partial.netlist.oa").write_text("partial", encoding="utf-8")
        raise MtsNetlistorError("injected importer failure")

    monkeypatch.setattr(publish, "publish_text_view", failing_text)
    result = publish.publish_bundle(
        request,
        session,
        netlist=tmp_path / "bufferx1.spe",
        run_dir=tmp_path / "run",
    )
    assert result.status == "manual_cleanup_required"
    assert result.affected_views == (str(view),)
    assert result.rollback == ()
    assert "injected importer failure" in result.message
    assert view.joinpath("partial.netlist.oa").read_text(encoding="utf-8") == "partial"


def test_manual_cleanup_result_contains_operator_diagnostics_and_evidence_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _target_cds, _source_lib, target_lib, session, request = _setup(tmp_path)
    request = NetlistRequest(
        request.source,
        request.dialect,
        request.models,
        request.process_options,
        request.simulator_options,
        TargetSelection("target", "renamed", False, True),
    ).validate()
    view = target_lib / "renamed" / "spectreText"
    monkeypatch.setattr(publication_preflight, "find_executable", lambda _value: "/fake/cdsTextTo5x")

    def failing_text(*_args: object, **_kwargs: object) -> object:
        view.mkdir(parents=True)
        raise MtsNetlistorError("injected importer failure")

    monkeypatch.setattr(publish, "publish_text_view", failing_text)
    result = publish.publish_bundle(
        request,
        session,
        netlist=tmp_path / "bufferx1.spe",
        run_dir=tmp_path / "run",
    )

    assert result.status == "manual_cleanup_required"
    assert result.affected_views == (str(view),)
    assert any(item == f"run_dir={tmp_path / 'run'}" for item in result.diagnostics)
    assert any(item == "target=target/renamed" for item in result.diagnostics)
    assert any(item.startswith("failure_type=MtsNetlistorError") for item in result.diagnostics)
    assert result.log_files == (str(tmp_path / "run" / "publish" / "nl2view.log"),)
    assert any("automatic OA rollback is not qualified" in item for item in result.cleanup_instructions)
    payload = result.to_dict()
    assert payload["diagnostics"] == list(result.diagnostics)
    assert payload["log_files"] == list(result.log_files)
    assert payload["cleanup_instructions"] == list(result.cleanup_instructions)
