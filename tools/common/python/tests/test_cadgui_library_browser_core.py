from cadgui_library_browser_fixtures import *

def test_empty_widget_exposes_catalog_and_three_column_handles(
    application: QApplication, tmp_path: Path
) -> None:
    browser = LibraryBrowserWidget()
    try:
        assert browser.catalog is None
        assert browser.selection is None
        assert browser.selected_library is None
        assert browser.selected_cell is None
        assert browser.selected_view is None
        assert isinstance(browser.splitter, QSplitter)
        assert browser.splitter.orientation() == Qt.Horizontal
        assert browser.splitter.count() == 3
        assert browser.library_list.count() == 0
        assert browser.cell_list.count() == 0
        assert browser.view_list.count() == 0

        catalog = _catalog(tmp_path)
        browser.set_catalog(catalog)
        assert browser.catalog is catalog
        assert browser.library_list.count() == 2
        assert browser.cell_list.count() == 2
        assert browser.view_list.count() == 2
        assert browser.selection == ("analog", "inv", "layout")
        assert browser.selected_library == catalog.libraries[0]
        assert browser.selected_cell == catalog.libraries[0].cells[0]
        assert browser.selected_view == catalog.libraries[0].cells[0].views[0]
    finally:
        browser.close()

def test_library_cell_view_selection_is_cascaded(
    application: QApplication, tmp_path: Path
) -> None:
    catalog = _catalog(tmp_path)
    browser = LibraryBrowserWidget(catalog)
    try:
        browser.library_list.setCurrentRow(1)
        assert browser.selected_library == catalog.libraries[1]
        assert [browser.cell_list.item(row).text() for row in range(browser.cell_list.count())] == [
            "core"
        ]
        assert browser.selection == ("digital", "core", "schematic")

        browser.library_list.setCurrentRow(0)
        browser.cell_list.setCurrentRow(1)
        assert browser.selection == ("analog", "pll", "schematic")
        assert browser.view_list.count() == 1
        assert browser.selected_view == catalog.libraries[0].cells[1].views[0]
    finally:
        browser.close()

def test_mouse_press_on_physical_library_survives_synchronous_list_rebuild(
    application: QApplication, tmp_path: Path
) -> None:
    browser = LibraryBrowserWidget(_catalog(tmp_path))
    browser.resize(720, 320)
    browser.show()
    application.processEvents()
    try:
        digital = browser.library_list.item(1)
        assert digital is not None
        position = browser.library_list.visualItemRect(digital).center()

        # Selecting a concrete QTreeWidgetItem synchronously rebuilds the
        # browser.  A real mouse event verifies that no succeeding native tree
        # signal observes the deleted item wrapper from the former tree.
        QTest.mouseClick(
            browser.library_list.viewport(),
            Qt.LeftButton,
            Qt.NoModifier,
            position,
        )
        application.processEvents()

        assert browser.selection == ("digital", "core", "schematic")
    finally:
        browser.close()

def test_filters_are_case_insensitive_substring_filters_and_reselect_visible_rows(
    application: QApplication, tmp_path: Path
) -> None:
    browser = LibraryBrowserWidget(_catalog(tmp_path))
    try:
        browser.library_filter.setText("DIG")
        assert _visible_texts(browser.library_list) == ["digital"]
        assert browser.selection == ("digital", "core", "schematic")

        browser.cell_filter.setText("ORE")
        assert _visible_texts(browser.cell_list) == ["core"]
        browser.view_filter.setText("SCH")
        assert _visible_texts(browser.view_list) == ["schematic"]

        browser.library_filter.clear()
        assert _visible_texts(browser.library_list) == ["analog", "digital"]
        # The current library remains selected when clearing a filter.
        assert browser.selected_library is not None
        assert browser.selected_library.name == "digital"
    finally:
        browser.close()

def test_replacing_catalog_preserves_existing_path_and_falls_back_when_missing(
    application: QApplication, tmp_path: Path
) -> None:
    original = _catalog(tmp_path / "original")
    browser = LibraryBrowserWidget(original)
    try:
        browser.select("analog", "inv", "schematic")
        replacement_root = tmp_path / "replacement" / "oa"
        replacement = Catalog(
            tmp_path / "replacement" / "cds.lib",
            (
                CatalogLibrary(
                    "analog",
                    replacement_root / "analog",
                    True,
                    (
                        CatalogCell(
                            "inv",
                            replacement_root / "analog" / "inv",
                            (CatalogView("layout", replacement_root / "analog" / "inv" / "layout"),),
                        ),
                    ),
                ),
            ),
            authoritative=True,
            provider="dbAccess",
        )
        browser.set_catalog(replacement)
        assert browser.catalog is replacement
        assert browser.selection == ("analog", "inv", "layout")

        browser.set_catalog(None)
        assert browser.catalog is None
        assert browser.selection is None
        assert browser.library_list.count() == 0
        assert browser.cell_list.count() == 0
        assert browser.view_list.count() == 0
    finally:
        browser.close()

def test_clear_keeps_filter_text_but_removes_catalog_content(
    application: QApplication, tmp_path: Path
) -> None:
    browser = LibraryBrowserWidget(_catalog(tmp_path))
    try:
        browser.library_filter.setText("ana")
        browser.cell_filter.setText("inv")
        browser.view_filter.setText("sch")
        browser.clear()
        assert browser.catalog is None
        assert browser.library_filter.text() == "ana"
        assert browser.cell_filter.text() == "inv"
        assert browser.view_filter.text() == "sch"
        assert browser.selection is None
    finally:
        browser.close()

def test_selection_changed_signal_contains_complete_name_tuple(
    application: QApplication, tmp_path: Path
) -> None:
    browser = LibraryBrowserWidget(_catalog(tmp_path))
    selection_spy = QSignalSpy(browser.selectionChanged)
    library_spy = QSignalSpy(browser.libraryChanged)
    cell_spy = QSignalSpy(browser.cellChanged)
    view_spy = QSignalSpy(browser.viewChanged)
    try:
        browser.view_list.setCurrentRow(1)
        assert browser.selection == ("analog", "inv", "schematic")
        assert selection_spy
        assert selection_spy[-1][0] == ("analog", "inv", "schematic")
        assert view_spy
        assert view_spy[-1][0].name == "schematic"

        browser.library_list.setCurrentRow(1)
        assert browser.selection == ("digital", "core", "schematic")
        assert library_spy[-1][0].name == "digital"
        assert cell_spy[-1][0].name == "core"
    finally:
        browser.close()

def test_activating_view_emits_view_activated(
    application: QApplication, tmp_path: Path
) -> None:
    browser = LibraryBrowserWidget(_catalog(tmp_path))
    browser.resize(720, 320)
    browser.show()
    application.processEvents()
    activated_spy = QSignalSpy(browser.viewActivated)
    try:
        item = browser.view_list.currentItem()
        assert item is not None
        # ``itemDoubleClicked`` is the widget's Qt activation input.  Emitting
        # it directly avoids relying on an offscreen platform plugin to
        # synthesize a complete native double-click sequence.
        browser.view_list.itemDoubleClicked.emit(item)
        application.processEvents()
        assert len(activated_spy) == 1
        assert list(activated_spy[0]) == ["analog", "inv", "layout"]
    finally:
        browser.close()

def test_view_activation_ignores_item_deleted_by_catalog_replacement(
    application: QApplication, tmp_path: Path
) -> None:
    browser = LibraryBrowserWidget(_catalog(tmp_path))
    activated_spy = QSignalSpy(browser.viewActivated)
    try:
        stale_view = browser.view_list.currentItem()
        assert stale_view is not None
        browser.set_catalog(None)

        browser._activate_view_item(stale_view)
        browser._activate_view_item(None)

        assert len(activated_spy) == 0
    finally:
        browser.close()

def test_invalid_catalog_type_is_rejected(
    application: QApplication,
) -> None:
    browser = LibraryBrowserWidget()
    try:
        with pytest.raises(TypeError, match="Catalog"):
            browser.set_catalog(object())
    finally:
        browser.close()

def test_negative_content_margin_is_rejected(application: QApplication) -> None:
    with pytest.raises(ValueError, match="content_top_margin"):
        LibraryBrowserWidget(content_top_margin=-1)

def test_qt_method_aliases_follow_the_python_api(
    application: QApplication, tmp_path: Path
) -> None:
    browser = LibraryBrowserWidget()
    try:
        browser.setCatalog(_catalog(tmp_path))
        assert browser.selection == ("analog", "inv", "layout")

        activated_spy = QSignalSpy(browser.viewActivated)
        assert browser.activateCurrentView()
        assert list(activated_spy[-1]) == ["analog", "inv", "layout"]

        browser.clearSelection()
        assert browser.selection is None
    finally:
        browser.close()
