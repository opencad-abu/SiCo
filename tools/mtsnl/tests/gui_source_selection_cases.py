"""gui source selection cases regressions."""

from __future__ import annotations
from pathlib import Path
import pytest
from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
)
from mtsnetlistor.gui.controller import ControllerState  # noqa: E402
from mtsnetlistor.gui.main_window import (  # noqa: E402
    MtsMainWindow,
)
from gui_window_fixtures import _draft, _view, _append_source_cell


def test_multicell_source_queue_copies_and_restores_per_cell_settings(
    application: QApplication, tmp_path: Path
) -> None:
    from cadview.catalog import Catalog, CatalogCell, CatalogLibrary, CatalogView
    from mtsnetlistor.catalog import CatalogResult

    cds = tmp_path / "cds.lib"
    library_path = tmp_path / "source"
    cds.write_text(f"DEFINE source {library_path}\n", encoding="utf-8")
    cells = []
    for cell_name in ("first", "second"):
        view_path = library_path / cell_name / "schematic"
        view_path.mkdir(parents=True)
        cells.append(CatalogCell(cell_name, view_path.parent, (CatalogView("schematic", view_path),)))
    catalog = Catalog(
        cds,
        (CatalogLibrary("source", library_path, True, tuple(cells), library_path),),
        True,
        "dbAccess",
    )
    model = tmp_path / "model.lib"
    model.write_text(".lib tt\n", encoding="utf-8")

    window = MtsMainWindow(session=None)
    try:
        window.source_edit.setText(str(cds))
        window._receive_state(
            ControllerState(source_catalog=CatalogResult(catalog, True, "dbAccess"))
        )
        window._poll_state()
        window.source_cell_list.setCurrentRow(0)
        window.source_view_list.setCurrentRow(0)
        window._select_source_view()
        window.temp.setValue(27.0)
        window._add_model()
        window._set_model_row(0, enabled=True, file=str(model), section="tt")

        window.source_cell_list.setCurrentRow(1)
        window.source_view_list.setCurrentRow(0)
        window._select_source_view()
        assert window.source_cells_list.count() == 2
        assert window.temp.value() == 27.0
        assert window._model_rows()[0][1] == str(model)

        window.temp.setValue(85.0)
        window.source_cells_list.setCurrentRow(0)
        assert window.temp.value() == 27.0
        request = window._request()
        assert [spec.cell for spec in request.cell_specs] == ["first", "second"]
        assert [spec.process_options.temp for spec in request.cell_specs] == [27.0, 85.0]
    finally:
        window.close()
        window.controller.close()


def test_combined_source_view_queues_physical_member_identity(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cadview.catalog import (
        Catalog,
        CatalogCell,
        CatalogCombineGroup,
        CatalogLibrary,
        CatalogView,
    )
    from mtsnetlistor.catalog import CatalogResult

    cds = tmp_path / "cds.lib"
    top_path = tmp_path / "TOP"
    member_path = tmp_path / "member"
    view_path = member_path / "shared" / "schematic"
    view_path.mkdir(parents=True)
    top_path.mkdir()
    cds.write_text(
        f"DEFINE TOP {top_path}\n"
        f"DEFINE member {member_path}\n"
        "ASSIGN TOP COMBINE member\n",
        encoding="utf-8",
    )
    catalog = Catalog(
        cds,
        (
            CatalogLibrary("TOP", top_path, True, (), top_path),
            CatalogLibrary(
                "member",
                member_path,
                True,
                (
                    CatalogCell(
                        "shared",
                        member_path / "shared",
                        (CatalogView("schematic", view_path),),
                    ),
                ),
                member_path,
            ),
        ),
        True,
        "dbAccess",
        combine_groups=(CatalogCombineGroup("TOP", ("member",)),),
    )
    probes: list[tuple[tuple[str, str, str], tuple[str, ...]]] = []
    window = MtsMainWindow(session=None)
    try:
        monkeypatch.setattr(
            window,
            "_enqueue_defaults_probe",
            lambda key, dialects: probes.append((key, tuple(dialects))),
        )
        window.source_edit.setText(str(cds))
        window._receive_state(
            ControllerState(
                source_catalog=CatalogResult(catalog, True, "dbAccess")
            )
        )
        window._poll_state()

        combined = window.source_library_list.topLevelItem(0)
        window.source_library_list.setCurrentItem(combined)
        window.source_cell_list.setCurrentRow(0)
        window.source_view_list.setCurrentRow(0)
        assert window.library_browser.selection == (
            "member",
            "shared",
            "schematic",
        )

        # Context-menu Select path.
        window._select_source_view()
        key = ("member", "shared", "schematic")
        assert window.source_cells_list.item(0).data(Qt.UserRole) == key
        assert probes == [(key, ("spectre", "hspiceD"))]
        request = window._request()
        assert request.cell_specs[0].library == "member"
        assert request.source.library == "member"
        assert request.source.cell == "shared"
        assert request.source.view == "schematic"

        # Browser activation/double-click path is idempotent and also carries
        # the exact physical member rather than the virtual TOP context.
        window.source_cells_list.clear()
        window.drafts.clear()
        probes.clear()
        window.library_browser.activate_current_view()
        assert window.source_cells_list.item(0).data(Qt.UserRole) == key
        assert probes == [(key, ("spectre", "hspiceD"))]
    finally:
        window.close()
        window.controller.close()


def test_deleting_active_and_last_source_cell_loads_or_clears_form(
    application: QApplication,
) -> None:
    window = MtsMainWindow(session=None)
    try:
        first = _draft("source", "first", "schematic", temp=27.0)
        second = _draft("source", "second", "schematic", temp=85.0)
        first_item = _append_source_cell(window, first)
        _append_source_cell(window, second)

        window.source_cells_list.setCurrentItem(first_item)
        window._delete_source_cell(first_item)
        assert window.source_cells_list.currentItem().text() == "source/second/schematic"
        assert window._active_cell_key == second.key
        assert window.temp.value() == 85.0

        window._delete_source_cell(window.source_cells_list.currentItem())
        assert window.source_cells_list.count() == 0
        assert window._active_cell_key is None
        assert window.temp.value() == window.temp.minimum()
        assert window.models_table.rowCount() == 0
        assert window.target_library.currentIndex() == -1
    finally:
        window.close()
        window.controller.close()


def test_new_source_cell_copies_simulation_settings_not_publication(
    application: QApplication, tmp_path: Path
) -> None:
    model = tmp_path / "model.lib"
    model.write_text(".lib tt\n", encoding="utf-8")
    window = MtsMainWindow(session=None)
    try:
        source = _draft(
            "source",
            "first",
            "schematic",
            models=((True, str(model), "tt", "core"),),
            temp=27.0,
            target_library="target",
            target_cell="renamed",
            publish_symbol=True,
            overwrite_symbol=True,
            publish_text=True,
            overwrite_text=True,
        )
        source_item = _append_source_cell(window, source)
        window.source_cells_list.setCurrentItem(source_item)
        window.source_view_list.addItem("schematic")
        candidate = window.source_view_list.item(0)
        candidate.setData(Qt.UserRole, ("source", "second", "schematic"))
        window.source_view_list.setCurrentItem(candidate)

        window._select_source_view()
        copied = _view(window, ("source", "second", "schematic"))
        assert copied.simulator.temp == "27.0"
        assert copied.simulator.models == source.simulator.models
        assert copied.publication.target_library == ""
        assert copied.publication.target_cell == ""
        assert not copied.publication.publish_symbol
        assert not copied.publication.overwrite_symbol
        assert not copied.publication.publish_text
        assert not copied.publication.overwrite_text
    finally:
        window.close()
        window.controller.close()
