"""gui generation identity cases regressions."""

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
from mtsnetlistor.gui.generation_coordinator import AcceptedGeneration
from mtsnetlistor.model import SourceDesign  # noqa: E402


def test_publish_reuses_successful_generation_source_identity(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cleared catalog selector must not produce an empty OA source library."""
    from cadview.catalog import Catalog, CatalogLibrary
    from mtsnetlistor.catalog import CatalogResult
    from mtsnetlistor.model import NetlistRequest

    cds = tmp_path / "source.cds.lib"
    source = tmp_path / "source"
    source.mkdir()
    cds.write_text(f"DEFINE source {source}\n", encoding="utf-8")
    target = tmp_path / "target"
    target.mkdir()
    target_cds = tmp_path / "target.cds.lib"
    target_cds.write_text(f"DEFINE target {target}\n", encoding="utf-8")
    request = NetlistRequest(SourceDesign(cds, "source", "top", "schematic")).validate()

    window = MtsMainWindow(session=None)
    try:
        window.generation.accepted = AcceptedGeneration(SimpleNamespace(
            stable_output=tmp_path / "top.spe", run_dir=tmp_path / "run"
        ), request)
        window.generation.result.stable_output.write_text("subckt top\nends top\n", encoding="utf-8")
        window.generation.result.run_dir.mkdir()
        window.publish_text.setChecked(True)
        window.target_library.addItem("target")
        window.target_library.setCurrentText("target")
        window.controller._state = ControllerState(
            target_catalog=CatalogResult(
                Catalog(target_cds, (CatalogLibrary("target", target, True, (), target),), True, "dbAccess"),
                True,
                "dbAccess",
            ),
            generation=window.generation.result,
            busy=False,
        )
        captured = {}
        monkeypatch.setattr(
            window.controller,
            "publish",
            lambda req, **kwargs: captured.update(request=req, kwargs=kwargs),
        )
        # Simulate a catalog refresh clearing the visible identity fields.
        window.source_library.clear()
        window.source_cell.clear()
        window.source_view.clear()
        window._publish()
        assert captured["request"].source.library == "source"
        assert captured["request"].source.cell == "top"
    finally:
        window.close()
        window.controller.close()


def test_generate_request_excludes_late_publication_selection(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cds = tmp_path / "cds.lib"
    library = tmp_path / "source"
    library.mkdir()
    cds.write_text(f"DEFINE source {library}\n", encoding="utf-8")
    window = MtsMainWindow(session=None)
    try:
        window.source_edit.setText(str(cds))
        window.source_library.setText("source")
        window.source_cell.setText("top")
        window.source_view.setText("schematic")
        window.publish_text.setChecked(True)
        window.target_library.addItem("target")
        captured = {}
        monkeypatch.setattr(
            window.controller,
            "generate",
            lambda request: captured.update(request=request) or 17,
        )
        window._generate()
        request = captured["request"]
        assert request.source.library == "source"
        assert request.target.library is None
        assert request.target.generate_netlist_view is False
        assert window.generation.pending[17].request is request
    finally:
        window.close()
        window.controller.close()


def test_consumed_generation_is_not_resurrected_by_catalog_state(
    application: QApplication,
) -> None:
    generation = object()
    window = MtsMainWindow(session=None)
    try:
        window.controller._state = ControllerState(generation=generation)
        window.generation.accepted = AcceptedGeneration(generation, None)
        window._invalidate_generation("test")
        window._receive_state(ControllerState(stage="source_catalog_ready", generation=generation))
        window._poll_state()
        assert window.generation.result is None
        assert window.generation.request is None
    finally:
        window.close()
        window.controller.close()


def test_stale_single_generation_is_not_displayed_after_source_switch(
    application: QApplication,
):
    window = MtsMainWindow(session=None)
    try:
        old_generation = object()
        window.controller._state = ControllerState(generation=old_generation)
        window.generation.accepted = AcceptedGeneration(old_generation, None)
        window._invalidate_generation("source switched")
        new_generation = SimpleNamespace(stable_output=Path("/tmp/stale.spe"))
        window._receive_state(ControllerState(stage="generation_ready", generation=new_generation))
        window._poll_state()
        assert window.generation.result is None
        assert window.generation.request is None
    finally:
        window.close()
        window.controller.close()


def test_netlist_input_edits_invalidate_generation_but_publication_edits_do_not(
    application: QApplication,
) -> None:
    window = MtsMainWindow(session=None)
    try:
        generation = object()
        window.generation.accepted = AcceptedGeneration(generation, None)
        window.temp.setValue(27.0)
        assert window.generation.result is None

        window.generation.accepted = AcceptedGeneration(generation, None)
        window.target_library.addItem("target")
        window.target_library.setCurrentText("target")
        window.target_cell.setText("renamed")
        window.publish_text.setChecked(True)
        window.overwrite_netlist_view.setChecked(True)
        assert window.generation.result is generation
    finally:
        window.close()
        window.controller.close()


def test_publish_rejects_netlist_input_digest_drift(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from cadview.catalog import Catalog, CatalogLibrary
    from mtsnetlistor.catalog import CatalogResult
    from mtsnetlistor.model import NetlistRequest

    cds = tmp_path / "source.cds.lib"
    source = tmp_path / "source"
    source.mkdir()
    cds.write_text(f"DEFINE source {source}\n", encoding="utf-8")
    request = NetlistRequest(SourceDesign(cds, "source", "top")).validate()
    target_cds = tmp_path / "target.cds.lib"
    target = tmp_path / "target"
    target.mkdir()
    target_cds.write_text(f"DEFINE target {target}\n", encoding="utf-8")

    window = MtsMainWindow(session=None)
    try:
        window.source_edit.setText(str(cds))
        window.source_library.setText("source")
        window.source_cell.setText("top")
        window.source_view.setText("schematic")
        window.generation.accepted = AcceptedGeneration(SimpleNamespace(
            stable_output=tmp_path / "top.spe", run_dir=tmp_path / "run"
        ), request)
        window.generation.result.stable_output.write_text("subckt top\nends top\n", encoding="utf-8")
        window.generation.result.run_dir.mkdir()
        window.publish_text.setChecked(True)
        window.target_library.addItem("target")
        window.target_library.setCurrentText("target")
        window.controller._state = ControllerState(
            target_catalog=CatalogResult(
                Catalog(target_cds, (CatalogLibrary("target", target, True, (), target),), True, "dbAccess"),
                True,
                "dbAccess",
            ),
            generation=window.generation.result,
            busy=False,
        )
        called = []
        monkeypatch.setattr(window.controller, "publish", lambda *_args, **_kwargs: called.append(True))
        window.temp.setValue(27.0)
        window.generation.accepted = AcceptedGeneration(window.generation.result or object(), request)
        # The generated request was captured with Temperature unset; the live
        # form now requests 27 C, so publication must stop before OA mutation.
        window._publish()
        assert called == []
        assert "regenerate before publication" in window.log.toPlainText()
    finally:
        window.close()
        window.controller.close()
