"""gui source catalog cases regressions."""

from __future__ import annotations
from pathlib import Path
import pytest
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
)
from mtsnetlistor.gui.controller import ControllerState  # noqa: E402
from mtsnetlistor.gui.main_window import (  # noqa: E402
    MtsMainWindow,
)
from gui_window_fixtures import _draft, _append_source_cell


def test_repeated_controller_state_keeps_selected_source_for_publish(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generation states retain catalogs and must not reset GUI selection."""

    from cadview.catalog import Catalog, CatalogCell, CatalogLibrary, CatalogView
    from mtsnetlistor.catalog import CatalogResult

    cds = tmp_path / "cds.lib"
    source_path = tmp_path / "source"
    view_path = source_path / "top" / "schematic"
    view_path.mkdir(parents=True)
    cds.write_text(f"DEFINE source {source_path}\n", encoding="utf-8")
    catalog = Catalog(
        cds,
        (
            CatalogLibrary(
                "source",
                source_path,
                True,
                (CatalogCell("top", source_path / "top", (CatalogView("schematic", view_path),)),),
                source_path,
            ),
        ),
        True,
        "dbAccess",
    )
    result = CatalogResult(catalog, True, "dbAccess")
    generation = object()

    window = MtsMainWindow(session=None)
    try:
        window.source_edit.setText(str(cds))
        first = ControllerState(stage="source_catalog_ready", source_catalog=result)
        window._receive_state(first)
        window._poll_state()

        # The catalog selectors cascade library -> cell -> view.
        window.source_library_list.setCurrentRow(0)
        window.source_cell_list.setCurrentRow(0)
        window.source_view_list.setCurrentRow(0)
        assert window.source_library.text() == "source"
        assert window.source_cell.text() == "top"
        assert window.source_view.text() == "schematic"

        # A generation result carries the same catalog object.  Previously
        # _poll_state repopulated the tree and cleared all three source fields.
        monkeypatch.setattr(window.log, "append", lambda _value: None)
        generated = ControllerState(
            stage="generation_ready",
            source_catalog=result,
            generation=generation,  # type: ignore[arg-type]
        )
        window._receive_state(generated)
        window._poll_state()

        assert window.source_library.text() == "source"
        assert window.source_cell.text() == "top"
        assert window.source_view.text() == "schematic"
    finally:
        window.close()
        window.controller.close()


def test_source_refresh_defers_target_refresh_until_source_is_displayed(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cadview.catalog import Catalog
    from mtsnetlistor.catalog import CatalogResult

    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="utf-8")
    calls = []
    window = MtsMainWindow(session=None)
    try:
        window.session = object()  # Only presence is relevant to GUI scheduling.
        monkeypatch.setattr(
            window.controller,
            "refresh_source_catalog",
            lambda path, **_kwargs: calls.append(("source", str(path))),
        )
        monkeypatch.setattr(
            window.controller,
            "refresh_target_catalog",
            lambda: calls.append(("target", None)),
        )
        window.source_edit.setText(str(cds))
        window._refresh_source()
        assert calls == [("source", str(cds))]
        assert window._target_refresh_requested

        source_result = CatalogResult(Catalog(cds, (), True, "dbAccess"), True, "dbAccess")
        window._receive_state(
            ControllerState(stage="source_catalog_ready", source_catalog=source_result)
        )
        window._poll_state()
        assert calls == [("source", str(cds)), ("target", None)]
        assert not window._target_refresh_requested
    finally:
        window.close()
        window.controller.close()


def test_explicit_source_refresh_bypasses_cache_but_project_restore_reuses_it(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="utf-8")
    calls = []
    window = MtsMainWindow(session=None)
    try:
        monkeypatch.setattr(
            window.controller,
            "refresh_source_catalog",
            lambda path, **kwargs: calls.append((str(path), kwargs)),
        )
        window.source_edit.setText(str(cds))
        window._refresh_source(force_refresh=True)
        assert calls[-1][1] == {"force_refresh": True}
        window._refresh_source(force_refresh=False)
        assert calls[-1][1] == {}
    finally:
        window.close()
        window.controller.close()


def test_explicit_source_refresh_preserves_legacy_manual_controller_signature(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="utf-8")
    calls = []
    window = MtsMainWindow(session=None)
    try:
        monkeypatch.setattr(
            window.controller,
            "refresh_source_catalog",
            lambda path: calls.append(str(path)),
        )
        window.source_edit.setText(str(cds))

        window._refresh_source(force_refresh=True)

        assert calls == [str(cds)]
    finally:
        window.close()
        window.controller.close()


def test_source_cdslib_switch_clears_old_queue_and_worker_context(
    application: QApplication, tmp_path: Path
) -> None:
    source_a = tmp_path / "a.cds.lib"
    source_b = tmp_path / "b.cds.lib"
    source_a.write_text("DEFINE sourceA ./sourceA\n", encoding="utf-8")
    source_b.write_text("DEFINE sourceB ./sourceB\n", encoding="utf-8")
    startup = tmp_path / "a.startup.il"
    simrc = tmp_path / "a.simrc"
    startup.write_text("a\n", encoding="utf-8")
    simrc.write_text("a\n", encoding="utf-8")

    window = MtsMainWindow(session=None)
    try:
        window.source_edit.setText(str(source_a))
        state = _draft("sourceA", "top", "schematic", temp=27.0)
        _append_source_cell(window, state)
        window._active_cell_key = state.key
        window._source_startup_file = startup
        window._source_simrc = simrc
        window._catalog = object()
        window._displayed_source_catalog = object()
        window._queue_mode = True

        window.source_edit.setText(str(source_b))

        assert window.source_cells_list.count() == 0
        assert not window.drafts
        assert window._active_cell_key is None
        assert window._source_startup_file is None
        assert window._source_simrc is None
        assert window._catalog is None
        assert window._displayed_source_catalog is None
        with pytest.raises(ValueError, match="Source Cells is empty"):
            window._request()
    finally:
        window.close()
        window.controller.close()


def test_delayed_source_catalog_for_old_cdslib_is_ignored(
    application: QApplication, tmp_path: Path
) -> None:
    from cadview.catalog import Catalog, CatalogLibrary
    from mtsnetlistor.catalog import CatalogResult

    source_a = tmp_path / "a.cds.lib"
    source_b = tmp_path / "b.cds.lib"
    source_a.write_text("DEFINE sourceA ./sourceA\n", encoding="utf-8")
    source_b.write_text("DEFINE sourceB ./sourceB\n", encoding="utf-8")
    old = CatalogResult(
        Catalog(source_a, (CatalogLibrary("sourceA", tmp_path, True, (), tmp_path),), True, "dbAccess"),
        True,
        "dbAccess",
    )
    current = CatalogResult(
        Catalog(source_b, (CatalogLibrary("sourceB", tmp_path, True, (), tmp_path),), True, "dbAccess"),
        True,
        "dbAccess",
    )
    window = MtsMainWindow(session=None)
    try:
        window.source_edit.setText(str(source_b))
        window._receive_state(ControllerState(source_catalog=old))
        window._poll_state()
        assert window.source_library_list.count() == 0
        assert window._displayed_source_catalog is None

        window._receive_state(ControllerState(source_catalog=current))
        window._poll_state()
        assert window.source_library_list.count() == 1
        assert window.source_library_list.item(0).text() == "sourceB"
    finally:
        window.close()
        window.controller.close()
