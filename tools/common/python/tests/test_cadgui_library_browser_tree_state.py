from cadgui_library_browser_fixtures import *

def test_library_filter_matches_group_name_or_individual_member(
    application: QApplication, tmp_path: Path
) -> None:
    base = _catalog(tmp_path)
    catalog = Catalog(
        base.cds_library_file,
        base.libraries,
        combine_groups=(CatalogCombineGroup("PROCESS_LIBS", ("analog", "digital")),),
    )
    browser = LibraryBrowserWidget(catalog)
    try:
        browser.library_filter.setText("process")
        assert browser.library_list.all_count() == 3
        assert [
            browser.library_list.item(row).text().strip()
            for row in range(browser.library_list.count())
        ] == ["analog", "digital"]

        browser.library_filter.setText("digit")
        assert browser.library_list.all_count() == 2
        assert browser.library_list.all_item(0).text() == "PROCESS_LIBS"
        assert browser.library_list.all_item(0).isExpanded()
        assert browser.library_list.item(0).text().strip() == "digital"
    finally:
        browser.close()

def test_nested_combine_groups_render_and_fold_recursively(
    application: QApplication, tmp_path: Path
) -> None:
    base = _catalog(tmp_path)
    catalog = Catalog(
        base.cds_library_file,
        base.libraries,
        combine_groups=(
            CatalogCombineGroup("newLib", ("analog",)),
            CatalogCombineGroup("testLib", ("newLib", "digital")),
        ),
    )
    browser = LibraryBrowserWidget(catalog)
    try:
        assert browser.library_list.all_count() == 4
        assert [
            browser.library_list.all_item(row).text().strip()
            for row in range(browser.library_list.all_count())
        ] == ["testLib", "newLib", "analog", "digital"]
        root = browser.library_list.topLevelItem(0)
        assert root.text() == "testLib"
        assert root.childCount() == 2
        assert root.child(0).text() == "newLib"
        assert root.child(0).child(0).text() == "analog"
        assert root.child(1).text() == "digital"
        assert not root.isExpanded()
        nested = browser.library_list.all_item(1)
        assert not nested.isExpanded()

        browser._library_item_pressed(root)
        assert root.isExpanded()
        assert nested.isHidden() is False
        assert browser.library_list.all_item(2).isHidden()
        assert not browser.library_list.all_item(3).isHidden()

        browser._library_item_pressed(nested)
        assert nested.isExpanded()
        assert not browser.library_list.all_item(2).isHidden()
        assert not browser.library_list.all_item(3).isHidden()
        assert browser.library_list.count() == 2
    finally:
        browser.close()

def test_same_named_nested_groups_keep_branch_specific_collapse_state(
    application: QApplication, tmp_path: Path
) -> None:
    base = _catalog(tmp_path)
    catalog = Catalog(
        base.cds_library_file,
        base.libraries,
        combine_groups=(
            CatalogCombineGroup("shared", ("analog",)),
            CatalogCombineGroup("ROOT_A", ("shared",)),
            CatalogCombineGroup("ROOT_B", ("shared", "digital")),
        ),
    )
    browser = LibraryBrowserWidget(catalog)
    try:
        assert browser.library_list.all_count() == 7
        first_root = browser.library_list.all_item(0)
        first_shared = browser.library_list.all_item(1)
        second_root = browser.library_list.all_item(3)
        second_shared = browser.library_list.all_item(4)
        browser._library_item_pressed(first_root)
        browser._library_item_pressed(first_shared)
        browser._library_item_pressed(second_root)
        assert not browser.library_list.all_item(2).isHidden()
        assert browser.library_list.all_item(5).isHidden()
        assert not browser.library_list.all_item(6).isHidden()
        assert second_shared.text().strip() == "shared"
        assert not second_shared.isExpanded()
    finally:
        browser.close()

def test_category_column_filters_everything_named_and_uncategorized_cells(
    application: QApplication, tmp_path: Path
) -> None:
    base = _catalog(tmp_path)
    analog = base.library("analog")
    assert analog is not None
    categorized = CatalogLibrary(
        analog.name,
        analog.path,
        analog.writable,
        analog.cells,
        analog.write_path,
        categories=(CatalogCategory("logic", ("inv",)),),
    )
    catalog = Catalog(
        base.cds_library_file,
        (categorized, base.libraries[1]),
        authoritative=True,
        provider="dbAccess",
    )
    browser = LibraryBrowserWidget(catalog)
    try:
        assert browser.splitter.count() == 3
        browser.show_categories.setChecked(True)
        assert browser.splitter.count() == 4
        assert isinstance(browser.category_list, CategoryTreeWidget)
        assert isinstance(browser.category_list, QTreeWidget)
        assert [browser.category_list.item(row).text() for row in range(3)] == [
            "Everything",
            "logic",
            "Uncategorized",
        ]

        browser.category_list.setCurrentRow(1)
        assert browser.selected_category == "logic"
        assert _visible_texts(browser.cell_list) == ["inv"]
        assert browser.selection == ("analog", "inv", "layout")

        browser.category_list.setCurrentRow(2)
        assert browser.selected_category == "Uncategorized"
        assert _visible_texts(browser.cell_list) == ["pll"]
        assert browser.selection == ("analog", "pll", "schematic")

        browser.show_categories.setChecked(False)
        assert browser.splitter.count() == 3
        assert _visible_texts(browser.cell_list) == ["inv", "pll"]

        browser.set_show_categories(True)
        assert browser.show_categories.isChecked()
        assert browser.splitter.count() == 4
    finally:
        browser.close()

def test_showing_category_equalizes_all_four_browser_columns(
    application: QApplication, tmp_path: Path
) -> None:
    browser = LibraryBrowserWidget(_catalog(tmp_path))
    browser.resize(1000, 360)
    browser.show()
    application.processEvents()
    try:
        browser.splitter.setSizes([150, 300, 550])
        browser.set_show_categories(True)
        application.processEvents()

        sizes = browser.splitter.sizes()
        assert len(sizes) == 4
        assert min(sizes) > 0
        assert max(sizes) - min(sizes) <= 1

        # A later explicit toggle starts from four equal columns again even
        # when the user had manually dragged the handles in the meantime.
        browser.splitter.setSizes([100, 200, 300, 400])
        browser.set_show_categories(False)
        browser.set_show_categories(True)
        application.processEvents()
        sizes = browser.splitter.sizes()
        assert max(sizes) - min(sizes) <= 1

        # Calling the enabled state again is idempotent and must not undo a
        # user's subsequent manual handle adjustment.
        browser.splitter.setSizes([100, 200, 300, 400])
        adjusted = browser.splitter.sizes()
        browser.set_show_categories(True)
        application.processEvents()
        assert browser.splitter.sizes() == adjusted
    finally:
        browser.close()
