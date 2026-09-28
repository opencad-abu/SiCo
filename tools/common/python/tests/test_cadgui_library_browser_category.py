from cadgui_library_browser_fixtures import *

def test_category_columns_equalize_when_enabled_before_first_show(
    application: QApplication, tmp_path: Path
) -> None:
    browser = LibraryBrowserWidget(_catalog(tmp_path))
    browser.resize(1000, 360)
    try:
        browser.set_show_categories(True)
        browser.show()
        application.processEvents()

        sizes = browser.splitter.sizes()
        assert len(sizes) == 4
        assert min(sizes) > 0
        assert max(sizes) - min(sizes) <= 1
    finally:
        browser.close()

def test_category_identity_handles_duplicate_paths_and_reserved_real_names(
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
        categories=(
            CatalogCategory(
                "parentA",
                (),
                (CatalogCategory("devices", ("inv",)),),
            ),
            CatalogCategory(
                "parentB",
                (),
                (CatalogCategory("devices", ("pll",)),),
            ),
            CatalogCategory("Everything", ("inv",)),
            CatalogCategory("Uncategorized", ("pll",)),
        ),
    )
    browser = LibraryBrowserWidget(
        Catalog(base.cds_library_file, (categorized, base.libraries[1]))
    )
    try:
        browser.show_categories.setChecked(True)
        displays = [
            browser.category_list.item(row).text()
            for row in range(browser.category_list.count())
        ]
        assert displays == [
            "Everything",
            "parentA",
            "devices",
            "parentB",
            "devices",
            "Everything (Category)",
            "Uncategorized (Category)",
            "Uncategorized",
        ]
        assert browser.category_list.topLevelItemCount() == 6
        parent_a = browser.category_list.topLevelItem(1)
        parent_b = browser.category_list.topLevelItem(2)
        assert parent_a.text() == "parentA"
        assert parent_a.childCount() == 1
        assert parent_a.child(0).text() == "devices"
        assert parent_b.text() == "parentB"
        assert parent_b.childCount() == 1
        assert parent_b.child(0).text() == "devices"

        browser.category_list.setCurrentRow(2)
        assert browser.selected_category == "parentA/devices"
        assert browser.selected_category_identity == (
            "category",
            ("parentA", "devices"),
        )
        assert _visible_texts(browser.cell_list) == ["inv"]

        browser.category_list.setCurrentRow(4)
        assert browser.selected_category == "parentB/devices"
        assert _visible_texts(browser.cell_list) == ["pll"]

        browser.category_list.setCurrentRow(5)
        assert browser.selected_category_identity == (
            "category",
            ("Everything",),
        )
        assert _visible_texts(browser.cell_list) == ["inv"]

        browser.category_list.setCurrentRow(0)
        assert browser.selected_category_identity == ("all", ())
        assert _visible_texts(browser.cell_list) == ["inv", "pll"]
    finally:
        browser.close()

def test_parent_category_includes_all_descendant_members(
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
        categories=(
            CatalogCategory(
                "logic",
                (),
                (
                    CatalogCategory("inverters", ("inv",)),
                    CatalogCategory("clocking", ("pll",)),
                ),
            ),
        ),
    )
    browser = LibraryBrowserWidget(
        Catalog(base.cds_library_file, (categorized, base.libraries[1]))
    )
    try:
        browser.show_categories.setChecked(True)
        browser.category_list.setCurrentRow(1)
        assert browser.selected_category == "logic"
        assert _visible_texts(browser.cell_list) == ["inv", "pll"]

        # The typed path survives an unrelated cell/view rebuild.
        browser.cell_list.setCurrentRow(1)
        assert browser.selected_category_identity == ("category", ("logic",))
        assert _visible_texts(browser.cell_list) == ["inv", "pll"]
    finally:
        browser.close()

def test_category_tree_filter_reveals_descendant_and_restores_parent_fold(
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
        categories=(
            CatalogCategory(
                "logic",
                (),
                (
                    CatalogCategory("inverters", ("inv",)),
                    CatalogCategory("clocking", ("pll",)),
                ),
            ),
        ),
    )
    browser = LibraryBrowserWidget(
        Catalog(base.cds_library_file, (categorized, base.libraries[1]))
    )
    try:
        browser.set_show_categories(True)
        tree = browser.category_list
        logic = tree.topLevelItem(1)
        assert logic.text() == "logic"
        assert logic.childCount() == 2
        assert [logic.child(index).text() for index in range(2)] == [
            "inverters",
            "clocking",
        ]

        # Real Category parents are folded when first shown.
        assert not logic.isExpanded()

        # Both explicit expansion and collapse survive ordinary rebuilds.
        logic.setExpanded(True)
        browser.cell_list.setCurrentRow(1)
        logic = tree.topLevelItem(1)
        assert logic.isExpanded()

        logic.setExpanded(False)
        browser.cell_list.setCurrentRow(0)
        logic = tree.topLevelItem(1)
        assert not logic.isExpanded()

        # A descendant-only match retains its ancestor as tree structure,
        # expands the path, and selects the actual match rather than the
        # broader ancestor Category.
        browser.category_filter.setText("clocking")
        assert tree.topLevelItemCount() == 1
        logic = tree.topLevelItem(0)
        assert logic.text() == "logic"
        assert logic.isExpanded()
        assert logic.childCount() == 1
        assert logic.child(0).text() == "clocking"
        assert browser.selected_category_identity == (
            "category",
            ("logic", "clocking"),
        )
        assert _visible_texts(browser.cell_list) == ["pll"]

        browser.category_filter.clear()
        logic = tree.topLevelItem(1)
        assert not logic.isExpanded()
        assert logic.childCount() == 2
    finally:
        browser.close()
