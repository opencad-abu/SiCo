"""Page-sized Qt views; queries and ordering are owned by the data worker."""

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sico.service.detail_content import CATEGORIES
from sico.service.workbench_query import PageQuery

from .glyph_plates import plate_arrow_icon, plate_text_icon
from .glyphs import ACTION_GLYPH_SIZE
from .presentation import BusyCursor


class ObjectList(QWidget):
    activated = pyqtSignal(str, str)
    queryChanged = pyqtSignal()

    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self.rows = ()
        self.offset, self.total, self.page_size, self.version = 0, 0, 200, -1
        self._selected_key = self._target_key = ""
        self._pending = False
        self._page = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setMaxLength(256)
        self.search.setPlaceholderText("搜索报告" if kind == "report" else "搜索数据")
        self.search.textChanged.connect(self.filter)
        layout.addWidget(self.search)
        self.category = QComboBox()
        self.category.addItem("全部数据", "")
        for key, label in CATEGORIES.items():
            self.category.addItem(label, key)
        self.category.currentIndexChanged.connect(self.filter)
        self.category.setVisible(kind == "data")
        layout.addWidget(self.category)
        self.source_filter = QComboBox()
        self.source_filter.addItem("全部来源", "")
        self.source_filter.currentIndexChanged.connect(self.filter)
        self.source_filter.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.source_filter.setMinimumContentsLength(10)
        layout.addWidget(self.source_filter)
        self.list = QTreeWidget()
        self.list.setHeaderLabels(["时间", "报告" if kind == "report" else "数据", "状态"])
        self.list.setRootIsDecorated(False)
        self.list.setVerticalScrollMode(QTreeWidget.ScrollPerPixel)
        self.list.setUniformRowHeights(True)
        self.list.header().setStretchLastSection(False)
        self.list.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.list.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.list.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.list.header().setSectionsClickable(True)
        self.list.header().sectionClicked.connect(self.sort_column)
        self.sort = "sequence_desc"
        self.list.currentItemChanged.connect(self.select)
        self.list.itemActivated.connect(lambda item, _: self.open(item))
        layout.addWidget(self.list, 1)
        # 查询期间只在列表里显示 busy，其余控件保持普通光标。
        self._busy = BusyCursor(self.list)
        paging = QHBoxLayout()
        self.previous = self.page_button(plate_arrow_icon(Qt.LeftArrow), "上一页", -1)
        self.next = self.page_button(plate_arrow_icon(Qt.RightArrow), "下一页", 1)
        self.count = QLabel("0–0 / 0")
        paging.addWidget(self.previous)
        paging.addWidget(self.count, 1)
        paging.addWidget(self.next)
        layout.addLayout(paging)
        footer = QHBoxLayout()
        self.summary = QLabel("暂无记录")
        self.summary.setTextFormat(Qt.PlainText)
        self.summary.setWordWrap(True)
        footer.addWidget(self.summary, 1)
        self.open_button = QToolButton()
        self.open_button.setIcon(plate_text_icon())
        self.open_button.setToolTip("打开详情")
        self.open_button.setEnabled(False)
        self.open_button.setIconSize(QSize(ACTION_GLYPH_SIZE, ACTION_GLYPH_SIZE))
        self.open_button.clicked.connect(lambda _checked=False: self.open(self.list.currentItem()))
        footer.addWidget(self.open_button)
        layout.addLayout(footer)

    def page_button(self, glyph, tooltip, step):
        button = QToolButton()
        button.setIcon(glyph)
        button.setIconSize(QSize(ACTION_GLYPH_SIZE, ACTION_GLYPH_SIZE))
        button.setToolTip(tooltip)
        button.setEnabled(False)
        button.clicked.connect(lambda _checked=False: self.page(step))
        return button

    def query(self):
        return PageQuery(
            keyword=self.search.text(), category=self.category.currentData() or "",
            source=self.source_filter.currentData() or "", sort=self.sort,
            offset=self.offset, page_size=self.page_size, selected_id=self._selected_key,
            locate_id=self._target_key, version=self.version,
        )

    def reset(self):
        for control in (self.search, self.category, self.source_filter):
            control.blockSignals(True)
        self.search.clear()
        self.category.setCurrentIndex(0)
        self.source_filter.clear()
        self.source_filter.addItem("全部来源", "")
        for control in (self.search, self.category, self.source_filter):
            control.blockSignals(False)
        self.rows, self._page = (), None
        self.offset, self.total, self.version = 0, 0, -1
        self._selected_key = self._target_key = ""
        self.list.clear()
        self.count.setText("0–0 / 0")
        self.set_pending(True)

    def set_pending(self, pending):
        self._pending = pending
        self._busy.set(pending)
        self.list.setEnabled(not pending)
        self.open_button.setEnabled(not pending and self.list.currentItem() is not None)
        if pending:
            self.count.setText("正在查询…")

    def filter(self, *args):
        self.offset = 0
        self._target_key = self._selected_key = ""
        self.queryChanged.emit()

    def page(self, step):
        self.offset = max(0, self.offset + step * self.page_size)
        self._target_key = self._selected_key = ""
        self.queryChanged.emit()

    def sort_column(self, column):
        name = ("sequence", "title", "status")[column]
        self.sort = name + ("_asc" if self.sort == name + "_desc" else "_desc")
        self.filter()

    def reveal(self, key):
        for control in (self.search, self.category, self.source_filter):
            control.blockSignals(True)
        self.search.clear()
        self.category.setCurrentIndex(0)
        self.source_filter.setCurrentIndex(0)
        for control in (self.search, self.category, self.source_filter):
            control.blockSignals(False)
        self._target_key = key
        self._selected_key = ""
        self.queryChanged.emit()

    def select_key(self, key):
        if not self._pending:
            for n in range(self.list.topLevelItemCount()):
                item = self.list.topLevelItem(n)
                if item.data(0, Qt.UserRole)["key"] == key:
                    if item is not self.list.currentItem():
                        self.list.setCurrentItem(item)
                        self.list.scrollToItem(item)
                    return True
        return False

    def apply_page(self, page):
        """Reconcile at most one page, retaining unchanged items and scroll."""
        position = self.list.verticalScrollBar().value()
        same_page = self._page is not None and self._page["offset"] == page["offset"]
        self._page = page
        self.rows = page["rows"]
        self.offset, self.total, self.version = page["offset"], page["total"], page["version"]
        self.page_size = page["page_size"]
        self._selected_key, self._target_key = page["selected_id"], ""
        options = ("", *page["sources"])
        old = tuple(self.source_filter.itemData(i) for i in range(self.source_filter.count()))
        if old != options:
            chosen = self.source_filter.currentData()
            self.source_filter.blockSignals(True)
            self.source_filter.clear()
            self.source_filter.addItem("全部来源", "")
            for key in page["sources"]:
                self.source_filter.addItem(key, key)
            self.source_filter.setCurrentIndex(max(0, self.source_filter.findData(chosen)))
            self.source_filter.blockSignals(False)
        self.list.blockSignals(True)
        items = {}
        while self.list.topLevelItemCount():
            item = self.list.takeTopLevelItem(0)
            items[item.data(0, Qt.UserRole)["key"]] = item
        selected = None
        for row in self.rows:
            key, label, stage = row["key"], row["label"], row["stage_title"]
            item = items.pop(key, None) or QTreeWidgetItem()
            for column, value in enumerate((row["stamp"], label[:240], row["state_label"])):
                if item.text(column) != value:
                    item.setText(column, value)
            item.setToolTip(1, label + "\n" + stage)
            item.setData(0, Qt.UserRole, {"key": key, "stage": stage, "title": label})
            self.list.addTopLevelItem(item)
            if key == self._selected_key:
                selected = item
        self.list.setCurrentItem(selected)
        self.list.blockSignals(False)
        self.set_pending(False)
        self.count.setText(
            f"{self.offset + 1 if self.total else 0}–"
            f"{self.offset + len(self.rows)} / {self.total}")
        self.previous.setEnabled(self.offset > 0)
        self.next.setEnabled(self.offset + self.page_size < self.total)
        self.select(selected)
        if not self.rows:
            filtered = (self.search.text() or self.category.currentData()
                        or self.source_filter.currentData())
            self.summary.setText("没有匹配的记录" if filtered else "暂无记录")
        if page["locate_id"] and not page["target_found"]:
            self.summary.setText("此记录暂不可用")
        if page["locate_id"] and selected is not None:
            self.list.scrollToItem(selected)
        elif same_page:
            self.list.verticalScrollBar().setValue(position)
        elif selected is not None:
            self.list.scrollToItem(selected)
        else:
            self.list.verticalScrollBar().setValue(0)

    def failed(self):
        self.set_pending(True)
        self._busy.set(False)
        self.count.setText("查询失败")
        self.summary.setText("工作台数据暂不可读，请稍后重试。")

    def select(self, item, previous=None):
        self._selected_key = item.data(0, Qt.UserRole)["key"] if item else ""
        self.open_button.setEnabled(item is not None and not self._pending)
        self.summary.setText(item.data(0, Qt.UserRole)["stage"] if item else "暂无选择")

    def open(self, item):
        if item is not None and not self._pending:
            self.activated.emit(self.kind, item.data(0, Qt.UserRole)["key"])
