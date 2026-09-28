from __future__ import annotations

import os
from pathlib import Path
from threading import Event
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt5")

from PyQt5.QtWidgets import QApplication  # noqa: E402

from cadgui import (  # noqa: E402
    CategoryTreeWidget,
    LibraryBrowserWidget,
    LibraryManagerWidget,
    LibraryTreeWidget,
)
from cadgui.library_browser_controller import CatalogController  # noqa: E402
from cadview import (  # noqa: E402
    Catalog,
    CatalogCell,
    CatalogCombineGroup,
    CatalogLibrary,
    CatalogView,
)


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _catalog(path: Path, name: str = "fixture") -> Catalog:
    root = path.parent / name
    view = CatalogView("schematic", root / "cell" / "schematic")
    cell = CatalogCell("cell", root / "cell", (view,))
    library = CatalogLibrary(name, root, True, (cell,), root)
    return Catalog(path, (library,), provider="fixture")


def _pump(application: QApplication, predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.005)
    application.processEvents()


def test_composite_loads_catalog_and_exposes_selection(
    application: QApplication, tmp_path: Path
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("", encoding="utf-8")

    def provider(path: Path, *, environ=None, cancel_event=None):
        return _catalog(path)

    widget = LibraryManagerWidget(
        provider="fixture",
        providers={"fixture": provider},
    )
    catalogs: list[Catalog] = []
    selections: list[object] = []
    widget.catalogReady.connect(catalogs.append)
    widget.selectionChanged.connect(selections.append)
    try:
        assert widget.load_cds_lib(cds) == 1
        _pump(application, lambda: bool(catalogs))
        assert widget.catalog is catalogs[0]
        assert widget.library_browser is widget.browser
        assert widget.selection == ("fixture", "cell", "schematic")
        assert selections[-1] == ("fixture", "cell", "schematic")
        assert widget.select("fixture", "cell", "schematic")
    finally:
        widget.shutdown()
        widget.deleteLater()


def test_composite_set_catalog_clear_and_activation(
    application: QApplication, tmp_path: Path
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("", encoding="utf-8")
    widget = LibraryManagerWidget(provider="filesystem")
    activated: list[tuple[str, str, str]] = []
    widget.viewActivated.connect(lambda *value: activated.append(tuple(value)))
    try:
        base = _catalog(cds)
        catalog = Catalog(
            base.cds_library_file,
            base.libraries,
            provider=base.provider,
            combine_groups=(CatalogCombineGroup("TOP", ("fixture",)),),
        )
        widget.set_catalog(catalog)
        combined = widget.browser.library_list.topLevelItem(0)
        widget.browser.library_list.setCurrentItem(combined)
        assert widget.selected_combined_library == "TOP"
        assert widget.selected_combined_library == widget.browser.selected_combined_library
        assert widget.selection == ("fixture", "cell", "schematic")
        assert widget.activate_current_view()
        assert activated == [("fixture", "cell", "schematic")]
        widget.browser.library_filter.setText("missing")
        widget.clear()
        assert widget.catalog is None
        assert widget.selection is None
        assert widget.browser.library_filter.text() == "missing"
    finally:
        widget.shutdown()
        widget.deleteLater()


def test_package_lazy_export_does_not_break_qt_access(
    application: QApplication,
) -> None:
    # The import above exercises __getattr__; this assertion documents the
    # public package-level facade promised to host applications.
    assert LibraryManagerWidget.__name__ == "LibraryManagerWidget"
    assert LibraryBrowserWidget.__name__ == "LibraryBrowserWidget"
    assert LibraryTreeWidget.__name__ == "LibraryTreeWidget"
    assert CategoryTreeWidget.__name__ == "CategoryTreeWidget"


def test_internally_created_controller_cannot_be_marked_external(
    application: QApplication,
) -> None:
    with pytest.raises(ValueError, match="injected controller"):
        LibraryManagerWidget(owns_controller=False)


def test_close_event_is_rejected_while_owned_controller_shutdown_times_out(
    application: QApplication, tmp_path: Path
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("", encoding="utf-8")
    started = Event()
    release = Event()

    def provider(path: Path, *, cancel_event=None):
        started.set()
        release.wait(2.0)
        return _catalog(path)

    widget = LibraryManagerWidget(
        provider="fixture",
        providers={"fixture": provider},
    )
    widget.show()
    widget.load_cds_lib(cds)
    _pump(application, started.is_set)

    original_shutdown = widget.shutdown
    widget.shutdown = lambda _timeout_ms=2_000: original_shutdown(0)
    try:
        assert widget.close() is False
        assert widget.isVisible()
        assert widget.controller._workers

        release.set()
        widget.shutdown = original_shutdown
        assert widget.close() is True
        assert widget.controller._workers == {}
    finally:
        release.set()
        widget.shutdown = original_shutdown
        widget.shutdown()
        widget.deleteLater()


def test_non_owning_widget_close_does_not_cancel_shared_controller(
    application: QApplication, tmp_path: Path
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("", encoding="utf-8")
    started = Event()
    release = Event()

    def provider(path: Path, *, cancel_event=None):
        started.set()
        release.wait(2.0)
        return _catalog(path)

    controller = CatalogController(providers={"fixture": provider}, provider="fixture")
    widget = LibraryManagerWidget(controller=controller)
    controller.submit(cds)
    _pump(application, started.is_set)
    try:
        assert widget.shutdown() is True
        assert not controller._closed
        assert controller._workers
        # A shared controller remains usable after the facade detaches.
        release.set()
        assert controller.shutdown(2_000) is True
    finally:
        release.set()
        controller.shutdown(2_000)
        widget.deleteLater()
