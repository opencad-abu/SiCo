"""One-column tree items with the legacy list-item calling convention."""

from __future__ import annotations

from PyQt5.QtWidgets import QTreeWidgetItem


class LibraryTreeItem(QTreeWidgetItem):
    """One-column QTreeWidgetItem with the historical list-style API."""

    def data(self, *args):
        if len(args) == 1:
            return super().data(0, args[0])
        return super().data(*args)

    def setData(self, *args):  # noqa: N802
        if len(args) == 2:
            return super().setData(0, args[0], args[1])
        return super().setData(*args)

    def text(self, *args):
        if not args:
            return super().text(0)
        return super().text(*args)

    def setText(self, *args):  # noqa: N802
        if len(args) == 1:
            return super().setText(0, args[0])
        return super().setText(*args)

    def setTextAlignment(self, *args):  # noqa: N802
        if len(args) == 1:
            return super().setTextAlignment(0, args[0])
        return super().setTextAlignment(*args)

    def setToolTip(self, *args):  # noqa: N802
        if len(args) == 1:
            return super().setToolTip(0, args[0])
        return super().setToolTip(*args)

    def toolTip(self, *args):  # noqa: N802
        if not args:
            return super().toolTip(0)
        return super().toolTip(*args)

    def isHidden(self):  # noqa: N802
        """Return effective visibility, including collapsed ancestors."""

        if super().isHidden():
            return True
        parent = self.parent()
        while parent is not None:
            if parent.isHidden() or not parent.isExpanded():
                return True
            parent = parent.parent()
        return False
