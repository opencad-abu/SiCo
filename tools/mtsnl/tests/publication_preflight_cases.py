"""publication preflight cases regressions."""

from __future__ import annotations
from pathlib import Path
import pytest
from mtsnetlistor import publish, publication_text, publication_preflight
from mtsnetlistor.errors import IsolationError, RequestValidationError
from mtsnetlistor.model import NetlistRequest, TargetSelection
from publication_fixtures import (
    _request,
    _setup,
)


def test_publish_rejects_library_not_bound_to_current_session(tmp_path: Path) -> None:
    source, _target_cds, _source_lib, _target_lib, session, _request_value = _setup(tmp_path)
    request = _request(source, target_library="other")
    with pytest.raises(RequestValidationError, match="current session"):
        publish.publish_text_view(
            request,
            session,
            netlist=tmp_path / "bufferx1.spe",
            run_dir=tmp_path / "run",
        )


def test_publish_rejects_session_target_path_change(tmp_path: Path) -> None:
    source, target_cds, _source_lib, target_lib, session, request = _setup(tmp_path)
    target_cds.write_text("DEFINE target ./changed-target-lib\n", encoding="utf-8")
    with pytest.raises(IsolationError, match="changed since session launch"):
        publish.publish_text_view(
            request,
            session,
            netlist=tmp_path / "bufferx1.spe",
            run_dir=tmp_path / "run",
        )


def test_existing_view_is_rejected_before_importer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, _target_cds, _source_lib, target_lib, session, request = _setup(tmp_path)
    view_directory = target_lib / "renamed" / "spectreText"
    view_directory.mkdir(parents=True)
    (view_directory / "existing.marker").write_text("keep", encoding="utf-8")
    called = False

    def fake_import(*_args: object, **_kwargs: object) -> int:
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(publication_text, "run_import", fake_import)
    monkeypatch.setattr(publication_preflight, "find_executable", lambda _: "/fake/cdsTextTo5x")
    with pytest.raises(RequestValidationError, match="already exists"):
        publish.publish_text_view(
            request,
            session,
            netlist=tmp_path / "bufferx1.spe",
            run_dir=tmp_path / "run",
        )
    assert called is False
    assert (view_directory / "existing.marker").read_text(encoding="utf-8") == "keep"


def test_symbol_and_text_existing_destinations_are_checked_independently(
    tmp_path: Path,
) -> None:
    source, _target_cds, _source_lib, target_lib, session, request = _setup(tmp_path)
    symbol = target_lib / "renamed" / "symbol"
    text = target_lib / "renamed" / "spectreText"
    symbol.mkdir(parents=True)
    text.mkdir(parents=True)

    only_symbol = NetlistRequest(
        request.source,
        request.dialect,
        request.models,
        request.process_options,
        request.simulator_options,
        TargetSelection(
            "target", "renamed", True, True, overwrite_symbol_view=True
        ),
    ).validate()
    with pytest.raises(RequestValidationError, match="spectreText"):
        publish.preflight_publication(
            only_symbol,
            session,
            netlist=tmp_path / "bufferx1.spe",
        )

    only_text = NetlistRequest(
        request.source,
        request.dialect,
        request.models,
        request.process_options,
        request.simulator_options,
        TargetSelection(
            "target", "renamed", True, True, overwrite_netlist_view=True
        ),
    ).validate()
    with pytest.raises(RequestValidationError, match="symbol"):
        publish.preflight_publication(
            only_text,
            session,
            netlist=tmp_path / "bufferx1.spe",
        )
