"""Restore cell/view rows safely after a browser rebuild."""

from __future__ import annotations

from typing import Optional, Union
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QListWidgetItem, QTreeWidgetItem
from cadview.catalog import CatalogView
from .library_tree import LibraryTreeWidget
from .library_sources import _ViewSource


def item_value(
    item: Optional[Union[QListWidgetItem, QTreeWidgetItem]]
):
    if item is None:
        return None
    try:
        return item.data(Qt.UserRole)
    except RuntimeError:
        # A synchronous catalog/list rebuild can delete the C++ item
        # while Qt is still completing the original input event.
        return None

def select_cell(item_list, preferred: Optional[str]) -> bool:
    selected = None
    if preferred is not None:
        for row in range(item_list.count()):
            candidate = item_list.item(row)
            if candidate.text() == preferred:
                selected = candidate
                break
    if selected is not None:
        item_list.setCurrentItem(selected)
        return True
    if item_list.count():
        item_list.setCurrentRow(0)
    else:
        item_list.setCurrentRow(-1)
    return False

def select_view(
    item_list,
    preferred: Optional[str],
    preferred_library: Optional[str],
) -> bool:
    selected = None
    if preferred is not None:
        for row in range(item_list.count()):
            candidate = item_list.item(row)
            source = candidate.data(LibraryTreeWidget.VIEW_SOURCE_ROLE)
            if isinstance(source, _ViewSource):
                if (
                    source.view.name == preferred
                    and (
                        preferred_library is None
                        or source.library.name == preferred_library
                    )
                ):
                    selected = candidate
                    break
            elif (
                isinstance(source, CatalogView)
                and source.name == preferred
                and preferred_library is None
            ):
                selected = candidate
                break
    if selected is not None:
        item_list.setCurrentItem(selected)
        return True
    if item_list.count():
        item_list.setCurrentRow(0)
    else:
        item_list.setCurrentRow(-1)
    return False
