from __future__ import annotations

import os
from pathlib import Path

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt5")

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtTest import QSignalSpy, QTest  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
    QSplitter,
    QTreeWidget,
)

from cadview.catalog import (  # noqa: E402
    Catalog,
    CatalogCategory,
    CatalogCell,
    CatalogCombineGroup,
    CatalogLibrary,
    CatalogView,
)
from cadgui.library_browser import (  # noqa: E402
    CategoryTreeWidget,
    LibraryBrowserWidget,
    LibraryTreeWidget,
)


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _catalog(tmp_path: Path) -> Catalog:
    root = tmp_path / "oa"

    def view(library: str, cell: str, name: str) -> CatalogView:
        return CatalogView(name, root / library / cell / name)

    analog = CatalogLibrary(
        "analog",
        root / "analog",
        True,
        (
            CatalogCell(
                "inv",
                root / "analog" / "inv",
                (
                    view("analog", "inv", "layout"),
                    view("analog", "inv", "schematic"),
                ),
            ),
            CatalogCell(
                "pll",
                root / "analog" / "pll",
                (view("analog", "pll", "schematic"),),
            ),
        ),
    )
    digital = CatalogLibrary(
        "digital",
        root / "digital",
        False,
        (
            CatalogCell(
                "core",
                root / "digital" / "core",
                (view("digital", "core", "schematic"),),
            ),
        ),
    )
    return Catalog(tmp_path / "cds.lib", (analog, digital), authoritative=True, provider="dbAccess")


def _combined_catalog(tmp_path: Path) -> Catalog:
    root = tmp_path / "oa"

    def cell(library: str, name: str, *views: str) -> CatalogCell:
        path = root / library / name
        return CatalogCell(
            name,
            path,
            tuple(CatalogView(view, path / view) for view in views),
        )

    top = CatalogLibrary(
        "TOP",
        root / "TOP",
        True,
        (
            cell("TOP", "shared", "schematic"),
            cell("TOP", "top_only", "symbol"),
        ),
        categories=(CatalogCategory("common", ("shared",)),),
    )
    member_a = CatalogLibrary(
        "A",
        root / "A",
        True,
        (
            cell("A", "a_only", "symbol"),
            cell("A", "mixed", "schematic"),
            cell("A", "shared", "layout", "schematic"),
        ),
        categories=(
            CatalogCategory(
                "common",
                ("shared",),
                (
                    CatalogCategory("nested", ("a_only",)),
                    CatalogCategory("mixedKind", ("mixed",)),
                ),
            ),
        ),
    )
    member_b = CatalogLibrary(
        "B",
        root / "B",
        True,
        (
            cell("B", "b_only", "symbol"),
            cell("B", "mixed", "schematic"),
            cell("B", "shared", "schematic", "verilog"),
        ),
        categories=(
            CatalogCategory(
                "common",
                ("shared",),
                (CatalogCategory("nested", ("b_only",)),),
            ),
        ),
    )
    return Catalog(
        tmp_path / "cds.lib",
        (top, member_a, member_b),
        authoritative=True,
        provider="dbAccess",
        combine_groups=(CatalogCombineGroup("TOP", ("A", "B")),),
    )


def _item_row_by_text(widget, text: str) -> int:
    for row in range(widget.count()):
        if widget.item(row).text() == text:
            return row
    raise AssertionError(f"missing row {text!r}")


def _visible_texts(widget) -> list[str]:
    return [
        widget.item(row).text()
        for row in range(widget.count())
        if not widget.item(row).isHidden()
    ]



__all__ = [name for name in globals() if not name.startswith("__")]
