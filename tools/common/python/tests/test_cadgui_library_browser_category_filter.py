from cadgui_library_browser_fixtures import *

def test_category_filter_falls_back_to_a_visible_category(
    application: QApplication, tmp_path: Path
) -> None:
    base = _catalog(tmp_path)
    analog = base.library("analog")
    categorized = CatalogLibrary(
        analog.name,
        analog.path,
        analog.writable,
        analog.cells,
        analog.write_path,
        categories=(CatalogCategory("logic", ("inv",)),),
    )
    browser = LibraryBrowserWidget(
        Catalog(base.cds_library_file, (categorized, base.libraries[1]))
    )
    try:
        browser.show_categories.setChecked(True)
        browser.category_list.setCurrentRow(1)
        assert browser.selected_category == "logic"

        browser.category_filter.setText("uncat")

        assert browser.selected_category_identity == ("uncategorized", ())
        assert _visible_texts(browser.cell_list) == ["pll"]

        browser.category_filter.setText("no such category")

        assert browser.selected_category_identity is None
        assert browser.cell_list.count() == 0
        assert browser.view_list.count() == 0
    finally:
        browser.close()
