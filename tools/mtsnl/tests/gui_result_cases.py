"""gui result cases regressions."""

from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace
import pytest
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
)
from mtsnetlistor.gui.controller import ControllerState  # noqa: E402
from mtsnetlistor.gui.main_window import (  # noqa: E402
    MtsMainWindow,
)
from mtsnetlistor.gui.result_browser import ResultRow, ResultView  # noqa: E402
from mtsnetlistor.model import SourceDesign  # noqa: E402
from gui_window_fixtures import _generated_artifact


def test_single_cell_generation_populates_and_enables_open_result(
    application: QApplication, tmp_path: Path
) -> None:
    from mtsnetlistor.config import canonical_request_digest
    from mtsnetlistor.model import NetlistRequest

    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="ascii")
    request = NetlistRequest(SourceDesign(cds, "source", "top")).validate()
    window = MtsMainWindow(session=None)
    page = window._pages[0]
    try:
        output = tmp_path / "top.spe"
        artifact = _generated_artifact(
            output,
            canonical_request_digest(page._target_free_request(request)),
        )
        page.generation.submitted(11, request)
        page._receive_state(ControllerState(token=11, generation=artifact, busy=False))
        page._poll_state()

        assert page.open_result_button.isEnabled()
        assert page._result_rows == (
            ResultRow("source", "top", "schematic", output),
        )
    finally:
        window.close()
        page.controller.close()


def test_multicell_generation_keeps_ordered_result_rows(
    application: QApplication, tmp_path: Path
) -> None:
    from mtsnetlistor.model import CellNetlistSpec, NetlistRequest
    from mtsnetlistor.workflow import CellGenerationResult, MultiGenerationResult

    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="ascii")
    request = NetlistRequest(  # noqa: F841 - retain original validated request setup
        SourceDesign(cds, "source", "first"),
        cell_specs=(CellNetlistSpec("source", "first"), CellNetlistSpec("source", "second")),
    ).validate()
    window = MtsMainWindow(session=None)
    page = window._pages[0]
    try:
        first = NetlistRequest(SourceDesign(cds, "source", "first")).validate()
        second = NetlistRequest(SourceDesign(cds, "source", "second")).validate()
        generation = MultiGenerationResult(
            "succeeded",
            (
                CellGenerationResult(first, _generated_artifact(tmp_path / "first.spe")),
                CellGenerationResult(second, _generated_artifact(tmp_path / "second.spe")),
            ),
        )
        page._set_generation_result_rows(generation)

        assert [row.source_cell for row in page._result_rows] == ["first", "second"]
        assert [row.netlist_file.name for row in page._result_rows] == ["first.spe", "second.spe"]
        assert page.open_result_button.isEnabled()
    finally:
        window.close()
        page.controller.close()


def test_publication_views_are_attached_to_the_matching_cell(
    application: QApplication, tmp_path: Path
) -> None:
    from mtsnetlistor.model import CellNetlistSpec, NetlistRequest
    from mtsnetlistor.publish import PublicationBundleResult
    from mtsnetlistor.workflow import CellGenerationResult, MultiGenerationResult

    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="ascii")
    request = NetlistRequest(  # noqa: F841 - retain original validated request setup
        SourceDesign(cds, "source", "first"),
        cell_specs=(CellNetlistSpec("source", "first"), CellNetlistSpec("source", "second")),
    ).validate()
    window = MtsMainWindow(session=None)
    page = window._pages[0]
    try:
        first = NetlistRequest(SourceDesign(cds, "source", "first")).validate()
        second = NetlistRequest(SourceDesign(cds, "source", "second")).validate()
        generation = MultiGenerationResult(
            "succeeded",
            (
                CellGenerationResult(first, _generated_artifact(tmp_path / "first.spe")),
                CellGenerationResult(second, _generated_artifact(tmp_path / "second.spe")),
            ),
        )
        page._set_generation_result_rows(generation)
        page._publication_requests = (second,)
        page._apply_publication_result_rows(
            PublicationBundleResult(
                status="succeeded",
                target_library="target",
                target_cell="second_mts",
                symbol=SimpleNamespace(status="succeeded"),
                text=SimpleNamespace(status="succeeded", target_view="spectreText"),
            )
        )

        assert page._result_rows[0].symbol_view is None
        assert page._result_rows[1].symbol_view == ResultView("target", "second_mts", "symbol")
        assert page._result_rows[1].netlist_view == ResultView("target", "second_mts", "spectreText")
        assert page._publication_requests == ()
    finally:
        window.close()
        page.controller.close()


def test_new_run_and_invalidation_clear_result_browser_state(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mtsnetlistor.model import NetlistRequest

    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="ascii")
    request = NetlistRequest(SourceDesign(cds, "source", "top")).validate()
    window = MtsMainWindow(session=None)
    page = window._pages[0]
    try:
        page._set_generation_result_rows(
            SimpleNamespace(stable_output=tmp_path / "top.spe"),
            request=request,
        )
        page._result_rows[0].netlist_file.write_text("top\n", encoding="ascii")
        assert page.open_result_button.isEnabled()
        monkeypatch.setattr(page, "_request", lambda: request)
        monkeypatch.setattr(page.controller, "generate", lambda _request: 22)
        assert page._run()
        assert page._result_rows == ()
        assert not page.open_result_button.isEnabled()

        page._set_generation_result_rows(
            SimpleNamespace(stable_output=tmp_path / "top.spe"),
            request=request,
        )
        page._invalidate_generation("test")
        assert page._result_rows == ()
        assert not page.open_result_button.isEnabled()
    finally:
        window.close()
        page.controller.close()


def test_open_result_view_requires_current_session_library(
    application: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = MtsMainWindow(session=None)
    page = window._pages[0]
    try:
        view = ResultView("target", "top_mts", "symbol")
        with pytest.raises(RuntimeError, match="launched from Virtuoso"):
            page._open_result_view(view)

        page.session = SimpleNamespace(target_library_paths={"known": "/tmp/known"})
        with pytest.raises(RuntimeError, match="current Virtuoso session"):
            page._open_result_view(view)

        requests: list[tuple[str, str, str]] = []
        monkeypatch.setattr(
            "mtsnetlistor.gui.process_results.emit_open_view_request",
            lambda library, cell, view: requests.append((library, cell, view)),
        )
        page._open_result_view(ResultView("known", "top_mts", "spectreText"))
        assert requests == [("known", "top_mts", "spectreText")]
    finally:
        window.close()
        page.controller.close()
