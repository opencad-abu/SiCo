"""gui publication target cases regressions."""

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
from gui_window_fixtures import _draft, _append_source_cell


def test_gui_overwrite_controls_are_independent_and_follow_generate_toggles(
    application: QApplication,
) -> None:
    window = MtsMainWindow(session=None)
    try:
        assert not window.overwrite_symbol_view.isEnabled()
        assert not window.overwrite_netlist_view.isEnabled()

        window.publish_symbol.setChecked(True)
        assert window.overwrite_symbol_view.isEnabled()
        assert not window.overwrite_netlist_view.isEnabled()
        window.overwrite_symbol_view.setChecked(True)

        window.publish_text.setChecked(True)
        assert window.overwrite_symbol_view.isChecked()
        assert window.overwrite_netlist_view.isEnabled()
        window.overwrite_netlist_view.setChecked(True)

        window.publish_symbol.setChecked(False)
        assert not window.overwrite_symbol_view.isEnabled()
        assert not window.overwrite_symbol_view.isChecked()
        assert window.overwrite_netlist_view.isEnabled()
        assert window.overwrite_netlist_view.isChecked()

        window.publish_text.setChecked(False)
        assert not window.overwrite_netlist_view.isEnabled()
        assert not window.overwrite_netlist_view.isChecked()
    finally:
        window.close()
        window.controller.close()


def test_run_multicell_publication_checks_all_queued_cells(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    cds = tmp_path / "source.cds.lib"
    source = tmp_path / "source"
    source.mkdir()
    cds.write_text(f"DEFINE source {source}\n", encoding="utf-8")
    window = MtsMainWindow(session=None)
    try:
        window.source_edit.setText(str(cds))
        first = _draft(
            "source", "first", "schematic", target_library="target",
            target_cell="first_out", publish_text=True,
        )
        second = _draft("source", "second", "schematic")
        first_item = _append_source_cell(window, first)
        _append_source_cell(window, second)
        window.source_cells_list.setCurrentItem(first_item)
        window.target_library.addItem("target")
        window.target_library.setCurrentText("target")
        window.controller._state = ControllerState(
            target_catalog=SimpleNamespace(authoritative=True), busy=False
        )
        captured = {}
        monkeypatch.setattr(
            window.controller,
            "generate_many",
            lambda request: captured.update(request=request) or 77,
        )
        window._run()
        assert window.generation.pending[77].publication is not None
        queued = window.generation.pending[77].publication
        assert queued.cell_specs[0].target.generate_netlist_view
        assert not queued.cell_specs[1].target.generate_netlist_view
    finally:
        window.close()
        window.controller.close()


def test_run_rejects_unknown_target_library_before_generation(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cadview.catalog import Catalog, CatalogLibrary
    from mtsnetlistor.catalog import CatalogResult

    cds = tmp_path / "source.cds.lib"
    source = tmp_path / "source"
    source.mkdir()
    cds.write_text(f"DEFINE source {source}\n", encoding="utf-8")
    target = tmp_path / "target"
    target.mkdir()
    target_cds = tmp_path / "target.cds.lib"
    target_cds.write_text(f"DEFINE target {target}\n", encoding="utf-8")
    window = MtsMainWindow(session=None)
    try:
        window.source_edit.setText(str(cds))
        window.source_library.setText("source")
        window.source_cell.setText("top")
        window.source_view.setText("schematic")
        window.publish_text.setChecked(True)
        window.target_library.addItem("not_in_project")
        window.target_library.setCurrentText("not_in_project")
        window.controller._state = ControllerState(
            target_catalog=CatalogResult(
                Catalog(
                    target_cds,
                    (CatalogLibrary("target", target, True, (), target),),
                    True,
                    "dbAccess",
                ),
                True,
                "dbAccess",
            ),
            busy=False,
        )
        generated = []
        monkeypatch.setattr(
            window.controller,
            "generate",
            lambda request: generated.append(request),
        )
        window._run()
        assert generated == []
        assert "not writable in the current project cds.lib" in window.log.toPlainText()
    finally:
        window.close()
        window.controller.close()
