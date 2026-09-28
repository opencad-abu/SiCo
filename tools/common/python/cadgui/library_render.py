"""Populate Qt browser columns from filtered catalog projections."""

from __future__ import annotations

from typing import Optional, Tuple
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QListWidgetItem
from .category_tree import CategoryTreeItem
from .library_tree import LibraryTreeWidget
from .library_groups import library_nodes, _LibraryLeaf, _LibraryNode
from .library_categories import (
    _CATEGORY_ALL, _CATEGORY_UNCATEGORIZED, _CategoryNode,
    _category_display, _category_item_label, _category_legacy_name,
    _merged_category_nodes, _filtered_category_node, cell_matches_category,
)
from .library_sources import _CellSource, _ViewSource
from .library_filter import matches


def populate_libraries(item_tree, catalog, filter_text):
    def render(
        node: _LibraryNode,
        depth: int = 0,
        ancestors: Tuple[str, ...] = (),
    ) -> None:
        if isinstance(node, _LibraryLeaf):
            item_tree.add_library_row(
                node.library,
                depth=depth,
                ancestors=ancestors,
            )
            return
        item_tree.add_group_row(
            node.name,
            depth=depth,
            ancestors=ancestors,
            libraries=node.libraries,
        )
        child_ancestors = (*ancestors, node.name)
        for child in node.children:
            render(child, depth + 1, child_ancestors)

    for node in library_nodes(catalog, filter_text):
        render(node)
    # While a filter is active every match is revealed, but the saved fold
    # state is restored as soon as the filter is cleared.
    item_tree.set_filter_active(bool(filter_text.strip()))


def populate_categories(item_tree, libraries, filter_text, preferred):
    nodes: list[_CategoryNode] = []
    for identity in (_CATEGORY_ALL,):
        if matches(_category_display(identity), filter_text):
            nodes.append(_CategoryNode(identity, (), True))
    nodes.extend(
        node
        for node in (
            _filtered_category_node(candidate, filter_text)
            for candidate in _merged_category_nodes(libraries)
        )
        if node is not None
    )
    if matches(
        _category_display(_CATEGORY_UNCATEGORIZED), filter_text
    ):
        nodes.append(
            _CategoryNode(_CATEGORY_UNCATEGORIZED, (), True)
        )

    def render(
        node: _CategoryNode,
        parent: Optional[CategoryTreeItem] = None,
    ) -> None:
        display = _category_item_label(node.identity)
        tooltip = _category_legacy_name(node.identity)
        item = item_tree.add_category_row(
            node.identity,
            display,
            parent=parent,
            directly_matched=node.directly_matched,
            tooltip=tooltip if tooltip != display else "",
        )
        for child in node.children:
            render(child, item)

    for node in nodes:
        render(node)
    item_tree.set_filter_active(bool(filter_text.strip()))
    requested = preferred or _CATEGORY_ALL
    item_tree.select_preferred(requested)


def populate_cells(item_list, libraries, category, categories_enabled, filter_text, combined):
    sources_by_name: dict[str, list[_CellSource]] = {}
    for library in libraries:
        for cell in library.cells:
            if not cell_matches_category(library, cell, category, categories_enabled):
                continue
            if not matches(cell.name, filter_text):
                continue
            sources_by_name.setdefault(cell.name, []).append(
                _CellSource(library, cell)
            )
    for cell_name in sorted(sources_by_name, key=str.casefold):
        sources = tuple(sources_by_name[cell_name])
        item = QListWidgetItem(cell_name)
        item.setData(Qt.UserRole, sources[0].cell)
        item.setData(LibraryTreeWidget.CELL_SOURCES_ROLE, sources)
        qualified = tuple(
            f"{source.library.name}/{source.cell.name}"
            for source in sources
        )
        if len(sources) > 1:
            item.setToolTip(
                f"{combined}/{cell_name} [COMBINED]\n"
                + "\n".join(qualified)
            )
        elif qualified:
            item.setToolTip(qualified[0])
        item_list.addItem(item)


def populate_views(item_list, sources, filter_text):
    rows: list[_ViewSource] = []
    duplicate_counts: dict[str, int] = {}
    for source in sources:
        for view in source.cell.views:
            if not matches(view.name, filter_text):
                continue
            row = _ViewSource(source.library, source.cell, view)
            rows.append(row)
            duplicate_counts[view.name] = duplicate_counts.get(view.name, 0) + 1
    rows.sort(
        key=lambda source: source.view.name.casefold()
    )
    for source in rows:
        display = source.view.name
        if duplicate_counts.get(source.view.name, 0) > 1:
            display = f"{display}[{source.library.name}]"
        item = QListWidgetItem(display)
        item.setData(Qt.UserRole, source.view)
        item.setData(LibraryTreeWidget.VIEW_SOURCE_ROLE, source)
        item.setToolTip(
            f"{source.library.name}/{source.cell.name}/{source.view.name}"
        )
        item_list.addItem(item)
