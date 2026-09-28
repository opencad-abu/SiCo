from cadgui_library_browser_fixtures import *

def test_clear_selection_removes_stale_child_rows_and_keeps_catalog(
    application: QApplication, tmp_path: Path
) -> None:
    browser = LibraryBrowserWidget(_catalog(tmp_path))
    try:
        browser.select("digital", "core", "schematic")

        browser.clear_selection()

        assert browser.catalog is not None
        assert browser.selection is None
        assert browser.library_list.count() == 2
        assert browser.library_list.currentItem() is None
        assert browser.cell_list.count() == 0
        assert browser.view_list.count() == 0

        # Selecting a library repopulates descendants from that library,
        # instead of acting on stale rows from the old parent selection.
        browser.library_list.setCurrentRow(0)
        assert browser.selection == ("analog", "inv", "layout")
    finally:
        browser.close()

def test_combine_groups_use_native_tree_and_keep_list_style_compatibility(
    application: QApplication, tmp_path: Path
) -> None:
    base = _catalog(tmp_path)
    catalog = Catalog(
        base.cds_library_file,
        base.libraries,
        authoritative=True,
        provider="dbAccess",
        combine_groups=(CatalogCombineGroup("TOP", ("analog", "digital")),),
    )
    browser = LibraryBrowserWidget(catalog)
    try:
        assert isinstance(browser.library_list, LibraryTreeWidget)
        assert isinstance(browser.library_list, QTreeWidget)
        assert browser.library_list.count() == 2
        assert browser.library_list.all_count() == 3
        assert browser.library_list.topLevelItemCount() == 1
        group = browser.library_list.topLevelItem(0)
        assert group is not None
        assert group.text() == "TOP"
        assert not group.isExpanded()
        assert group.childCount() == 2
        assert group.child(0).text() == "analog"
        assert group.child(1).text() == "digital"
        assert [browser.library_list.item(row).text().strip() for row in range(2)] == [
            "analog",
            "digital",
        ]
        assert browser.library_list.all_item(1).isHidden()
        assert browser.library_list.all_item(2).isHidden()

        browser._library_item_pressed(group)
        assert group.isExpanded()
        assert not browser.library_list.all_item(1).isHidden()
        assert not browser.library_list.all_item(2).isHidden()

        browser._library_item_pressed(group)
        assert not group.isExpanded()
        assert browser.library_list.all_item(1).isHidden()
        assert browser.library_list.all_item(2).isHidden()
    finally:
        browser.close()

def test_combine_toggle_ignores_item_deleted_by_list_rebuild(
    application: QApplication, tmp_path: Path
) -> None:
    base = _catalog(tmp_path)
    browser = LibraryBrowserWidget(
        Catalog(
            base.cds_library_file,
            base.libraries,
            combine_groups=(CatalogCombineGroup("TOP", ("analog", "digital")),),
        )
    )
    try:
        stale_group = browser.library_list.all_item(0)
        assert stale_group is not None
        browser.library_list.clear()

        assert browser.library_list.toggle_group(stale_group) is False
        assert browser.library_list.toggle_group(None) is False
    finally:
        browser.close()

def test_selecting_combined_library_aggregates_cells_and_physical_views(
    application: QApplication, tmp_path: Path
) -> None:
    browser = LibraryBrowserWidget(_combined_catalog(tmp_path))
    activated = QSignalSpy(browser.viewActivated)
    try:
        combined = browser.library_list.topLevelItem(0)
        assert combined.text() == "TOP"
        assert combined.flags() & Qt.ItemIsSelectable
        assert combined.toolTip() == "TOP [COMBINED]"

        # A folded group is selectable independently from its branch arrow.
        browser.library_list.setCurrentItem(combined)
        assert browser.selected_combined_library == "TOP"
        assert browser.selected_library is not None
        assert browser.selected_library.name == "TOP"
        assert [
            browser.cell_list.item(row).text()
            for row in range(browser.cell_list.count())
        ] == ["a_only", "b_only", "mixed", "shared", "top_only"]

        shared_row = _item_row_by_text(browser.cell_list, "shared")
        browser.cell_list.setCurrentRow(shared_row)
        assert "TOP/shared [COMBINED]" in browser.cell_list.currentItem().toolTip()
        assert browser.cell_list.currentItem().toolTip().endswith(
            "TOP/shared\nA/shared\nB/shared"
        )
        assert [
            browser.view_list.item(row).text()
            for row in range(browser.view_list.count())
        ] == [
            "layout",
            "schematic[TOP]",
            "schematic[A]",
            "schematic[B]",
            "verilog",
        ]

        b_view = _item_row_by_text(browser.view_list, "schematic[B]")
        browser.view_list.setCurrentRow(b_view)
        assert browser.selection == ("B", "shared", "schematic")
        assert browser.view_list.currentItem().toolTip() == "B/shared/schematic"
        assert browser.activate_current_view()
        assert list(activated[-1]) == ["B", "shared", "schematic"]
    finally:
        browser.close()

def test_combined_view_source_survives_filter_category_and_catalog_rebuilds(
    application: QApplication, tmp_path: Path
) -> None:
    catalog = _combined_catalog(tmp_path)
    browser = LibraryBrowserWidget(catalog)
    changed = QSignalSpy(browser.selectionChanged)
    try:
        browser.library_list.setCurrentItem(browser.library_list.topLevelItem(0))
        browser.cell_list.setCurrentRow(_item_row_by_text(browser.cell_list, "shared"))

        a_view = _item_row_by_text(browser.view_list, "schematic[A]")
        b_view = _item_row_by_text(browser.view_list, "schematic[B]")
        browser.view_list.setCurrentRow(a_view)
        assert browser.selection == ("A", "shared", "schematic")
        browser.view_list.setCurrentRow(b_view)
        assert browser.selection == ("B", "shared", "schematic")
        assert tuple(changed[-1][0]) == ("B", "shared", "schematic")

        browser.cell_filter.setText("sha")
        assert browser.selection == ("B", "shared", "schematic")
        browser.set_show_categories(True)
        assert browser.selection == ("B", "shared", "schematic")
        browser.category_filter.setText("common")
        assert browser.selection == ("B", "shared", "schematic")
        browser.category_filter.clear()
        browser.set_catalog(catalog)
        assert browser.selection == ("B", "shared", "schematic")

        # A library-name filter changes only the visible tree branch.  The
        # selected COMBINE context still contains TOP, A and B data.
        browser.library_filter.setText("A")
        assert browser.selected_combined_library == "TOP"
        assert browser.selection == ("B", "shared", "schematic")
        assert browser.cell_list.count() == 1
    finally:
        browser.close()

def test_combined_categories_merge_paths_and_track_occurrence_membership(
    application: QApplication, tmp_path: Path
) -> None:
    browser = LibraryBrowserWidget(_combined_catalog(tmp_path))
    try:
        browser.library_list.setCurrentItem(browser.library_list.topLevelItem(0))
        browser.set_show_categories(True)
        identities = [
            browser.category_list.item(row).data(Qt.UserRole)
            for row in range(browser.category_list.count())
        ]
        assert identities.count(("category", ("common",))) == 1
        assert identities.count(("category", ("common", "nested"))) == 1

        nested = identities.index(("category", ("common", "nested")))
        browser.category_list.setCurrentRow(nested)
        assert [
            browser.cell_list.item(row).text()
            for row in range(browser.cell_list.count())
        ] == ["a_only", "b_only"]

        common = identities.index(("category", ("common",)))
        browser.category_list.setCurrentRow(common)
        assert [
            browser.cell_list.item(row).text()
            for row in range(browser.cell_list.count())
        ] == ["a_only", "b_only", "mixed", "shared"]

        uncategorized = identities.index(("uncategorized", ()))
        browser.category_list.setCurrentRow(uncategorized)
        # B/mixed is uncategorized even though A/mixed belongs to mixedKind;
        # the combined Cell row must therefore remain visible.
        assert [
            browser.cell_list.item(row).text()
            for row in range(browser.cell_list.count())
        ] == ["mixed", "top_only"]
    finally:
        browser.close()

def test_nested_combined_library_includes_physical_group_data_transitively(
    application: QApplication, tmp_path: Path
) -> None:
    root = tmp_path / "oa"

    def library(name: str, cell_name: str) -> CatalogLibrary:
        cell_path = root / name / cell_name
        return CatalogLibrary(
            name,
            root / name,
            True,
            (
                CatalogCell(
                    cell_name,
                    cell_path,
                    (CatalogView("schematic", cell_path / "schematic"),),
                ),
            ),
        )

    catalog = Catalog(
        tmp_path / "cds.lib",
        (
            library("TOP", "top_cell"),
            library("INNER", "inner_cell"),
            library("A", "a_cell"),
            library("B", "b_cell"),
        ),
        combine_groups=(
            CatalogCombineGroup("INNER", ("A",)),
            CatalogCombineGroup("TOP", ("INNER", "B", "A")),
        ),
    )
    browser = LibraryBrowserWidget(catalog)
    try:
        top = browser.library_list.topLevelItem(0)
        inner = top.child(0)
        browser.library_list.setCurrentItem(top)
        assert [
            browser.cell_list.item(row).text()
            for row in range(browser.cell_list.count())
        ] == ["a_cell", "b_cell", "inner_cell", "top_cell"]

        # Selecting the nested group uses its own full aggregate, without TOP/B.
        inner = browser.library_list.topLevelItem(0).child(0)
        browser.library_list.setCurrentItem(inner)
        assert [
            browser.cell_list.item(row).text()
            for row in range(browser.cell_list.count())
        ] == ["a_cell", "inner_cell"]
    finally:
        browser.close()

def test_combine_tree_supports_native_keyboard_expand_and_collapse(
    application: QApplication, tmp_path: Path
) -> None:
    base = _catalog(tmp_path)
    browser = LibraryBrowserWidget(
        Catalog(
            base.cds_library_file,
            base.libraries,
            combine_groups=(CatalogCombineGroup("TOP", ("analog", "digital")),),
        )
    )
    browser.resize(720, 320)
    browser.show()
    application.processEvents()
    try:
        tree = browser.library_list
        group = tree.topLevelItem(0)
        tree.setCurrentItem(group)
        tree.setFocus()
        group = tree.currentItem()

        QTest.keyClick(tree, Qt.Key_Left)
        application.processEvents()
        group = tree.currentItem()
        assert not group.isExpanded()

        QTest.keyClick(tree, Qt.Key_Right)
        application.processEvents()
        group = tree.currentItem()
        assert group.isExpanded()
    finally:
        browser.close()

def test_combine_group_collapse_does_not_hide_following_standalone_library(
    application: QApplication, tmp_path: Path
) -> None:
    base = _catalog(tmp_path)
    catalog = Catalog(
        base.cds_library_file,
        base.libraries,
        combine_groups=(CatalogCombineGroup("TOP", ("analog",)),),
    )
    browser = LibraryBrowserWidget(catalog)
    try:
        assert browser.library_list.all_count() == 3
        group = browser.library_list.all_item(0)
        standalone = browser.library_list.all_item(2)
        assert group is not None and standalone is not None
        assert standalone.text() == "digital"
        assert not group.isExpanded()
        assert browser.library_list.all_item(1).isHidden()
        assert not standalone.isHidden()
        assert browser.library_list.count() == 2

        browser._library_item_pressed(group)
        assert not browser.library_list.all_item(1).isHidden()
        assert not standalone.isHidden()
    finally:
        browser.close()

def test_multiple_combine_groups_collapse_independently(
    application: QApplication, tmp_path: Path
) -> None:
    base = _catalog(tmp_path)
    catalog = Catalog(
        base.cds_library_file,
        base.libraries,
        combine_groups=(
            CatalogCombineGroup("GROUP_A", ("analog",)),
            CatalogCombineGroup("GROUP_B", ("digital",)),
        ),
    )
    browser = LibraryBrowserWidget(catalog)
    try:
        assert browser.library_list.all_count() == 4
        first = browser.library_list.all_item(0)
        second = browser.library_list.all_item(2)
        assert first is not None and second is not None
        assert not first.isExpanded()
        assert not second.isExpanded()
        assert browser.library_list.all_item(1).isHidden()
        assert browser.library_list.all_item(3).isHidden()

        browser._library_item_pressed(first)
        assert not browser.library_list.all_item(1).isHidden()
        assert not browser.library_list.all_item(2).isHidden()
        assert browser.library_list.all_item(3).isHidden()
        browser._library_item_pressed(second)
        assert not browser.library_list.all_item(3).isHidden()
        browser._library_item_pressed(first)
        assert browser.library_list.all_item(1).isHidden()
        assert not browser.library_list.all_item(3).isHidden()
    finally:
        browser.close()

def test_combine_members_deduplicate_within_group_but_repeat_across_groups(
    application: QApplication, tmp_path: Path
) -> None:
    base = _catalog(tmp_path)
    catalog = Catalog(
        base.cds_library_file,
        base.libraries,
        combine_groups=(
            CatalogCombineGroup("GROUP_A", ("analog", "analog", "digital")),
            CatalogCombineGroup("GROUP_B", ("digital", "analog")),
        ),
    )
    browser = LibraryBrowserWidget(catalog)
    try:
        # Each group contains analog/digital once.  Cadence allows a physical
        # library to occur under more than one combined library, so the tree
        # has two group roots plus four child occurrences.
        assert browser.library_list.all_count() == 6
        assert browser.library_list.count() == 2
        names = [
            browser.library_list.item(row).text().strip()
            for row in range(browser.library_list.count())
        ]
        assert names == ["analog", "digital"]
        assert browser.library_list.topLevelItemCount() == 2
        assert browser.library_list.topLevelItem(0).text() == "GROUP_A"
        assert browser.library_list.topLevelItem(1).text() == "GROUP_B"
        assert browser.library_list.topLevelItem(0).childCount() == 2
        assert browser.library_list.topLevelItem(1).childCount() == 2
    finally:
        browser.close()

def test_combine_collapse_state_survives_selection_and_filter_rebuilds(
    application: QApplication, tmp_path: Path
) -> None:
    base = _catalog(tmp_path)
    catalog = Catalog(
        base.cds_library_file,
        base.libraries,
        combine_groups=(CatalogCombineGroup("TOP", ("analog", "digital")),),
    )
    browser = LibraryBrowserWidget(catalog)
    try:
        group = browser.library_list.all_item(0)
        assert not group.isExpanded()

        # Selecting a cell causes a full browser rebuild; the saved group
        # state must still hide the newly-created member rows afterwards.
        browser.cell_list.setCurrentRow(1)
        rebuilt = browser.library_list.all_item(0)
        assert rebuilt.text() == "TOP"
        assert not rebuilt.isExpanded()
        assert browser.library_list.all_item(1).isHidden()

        # An explicit expansion is also retained across an ordinary rebuild.
        browser._library_item_pressed(rebuilt)
        assert rebuilt.isExpanded()
        browser.cell_list.setCurrentRow(0)
        rebuilt = browser.library_list.all_item(0)
        assert rebuilt.isExpanded()

        # A filter exposes matching results temporarily.  Clearing it restores
        # the saved user state instead of changing it.
        browser._library_item_pressed(rebuilt)
        assert not rebuilt.isExpanded()
        browser.library_filter.setText("analog")
        assert browser.library_list.all_item(0).isExpanded()
        assert not browser.library_list.all_item(1).isHidden()
        browser.library_filter.clear()
        assert not browser.library_list.all_item(0).isExpanded()
        assert browser.library_list.all_item(1).isHidden()
    finally:
        browser.close()

def test_combine_collapse_state_resets_for_a_different_catalog_source(
    application: QApplication, tmp_path: Path
) -> None:
    first_base = _catalog(tmp_path / "first")
    first = Catalog(
        first_base.cds_library_file,
        first_base.libraries,
        combine_groups=(CatalogCombineGroup("TOP", ("analog", "digital")),),
    )
    second_base = _catalog(tmp_path / "second")
    second = Catalog(
        second_base.cds_library_file,
        second_base.libraries,
        combine_groups=(CatalogCombineGroup("TOP", ("analog", "digital")),),
    )
    browser = LibraryBrowserWidget(first)
    try:
        browser._library_item_pressed(browser.library_list.all_item(0))
        assert browser.library_list.all_item(0).isExpanded()

        browser.set_catalog(second)

        assert not browser.library_list.all_item(0).isExpanded()
        assert browser.library_list.all_item(1).isHidden()
    finally:
        browser.close()

def test_combine_cycles_are_bounded_and_keep_physical_members_visible(
    application: QApplication, tmp_path: Path
) -> None:
    base = _catalog(tmp_path)
    catalog = Catalog(
        base.cds_library_file,
        base.libraries,
        combine_groups=(
            CatalogCombineGroup("A", ("B",)),
            CatalogCombineGroup("B", ("A", "analog")),
        ),
    )
    browser = LibraryBrowserWidget(catalog)
    try:
        # A malformed circular cds.lib has no natural root.  The defensive
        # renderer presents each definition once as a root and truncates only
        # its back edge, rather than recursing forever or losing valid leaves.
        assert browser.library_list.all_count() == 7
        assert browser.library_list.count() == 2
        assert browser.library_list.item(0).data(Qt.UserRole).name == "analog"
        assert browser.library_list.item(1).data(Qt.UserRole).name == "digital"
    finally:
        browser.close()
