"""Assemble and operate the DRC selection dialog."""

from __future__ import annotations

from pathlib import Path
from cadgui.branding import logo_text
from .rule_groups import RuleGroupInfo
from .rule_selection import RuleSelection, write_selection
from .rule_select_model import NAME_ROLE, RuleSelectModel
from .rule_select_view import RuleSelectFilterProxyModel, RuleTreeView
from .rule_select_style import logo_path
from .rule_select_qt import (
    QCloseEvent, QDialogButtonBox, QHBoxLayout, QHeaderView,
    QIcon, QLabel, QLineEdit, QPushButton, QSignalBlocker,
    QStyle, QTimer, Qt, QTreeView, QVBoxLayout, WheelForwardingFilter,
    SiDialog, notice,
)


class RuleSelectDialog(SiDialog):
    """Modeless-style selector hosted in its own Python process."""

    def __init__(
        self,
        groups: RuleGroupInfo,
        *,
        output_path: str | Path,
        initial: RuleSelection | None = None,
        rule_file: str | Path | None = None,
        parent=None,
    ) -> None:
        super().__init__(f"{logo_text()}::DRC Rule Select", parent)
        self.output_path = Path(output_path).expanduser()
        self.rule_file = Path(rule_file).expanduser() if rule_file else None
        self.applied = False
        self._wheel_filter: WheelForwardingFilter | None = None
        self.model = RuleSelectModel(groups, initial, self)
        self.proxy = RuleSelectFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(120)
        self._build_ui()
        self._connect_ui()
        self._update_status()

    def _build_ui(self) -> None:
        logo = logo_path()
        if logo is not None:
            self.setWindowIcon(QIcon(str(logo)))
        self.resize(660, 680)
        self.setMinimumSize(480, 400)

        self.search = QLineEdit(self)
        self.search.setObjectName("ruleFilter")
        self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText("Filter groups and checks")

        self.tree = RuleTreeView(self)
        self.tree.setObjectName("ruleTree")
        self.tree.setModel(self.proxy)
        self.tree.setUniformRowHeights(True)
        self.tree.setAnimated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setItemsExpandable(True)
        self.tree.setExpandsOnDoubleClick(True)
        self.tree.setAllColumnsShowFocus(True)
        self.tree.setSelectionMode(QTreeView.SingleSelection)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.resizeSection(1, 80)

        self.status = QLabel(self)
        self.status.setObjectName("ruleStatus")
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        if self.model.info.error:
            self.status.setToolTip(self.model.info.error)

        self.select_visible_button = QPushButton("Select Visible", self)
        self.select_visible_button.setObjectName("selectVisible")
        self.select_visible_button.setIcon(
            self.style().standardIcon(QStyle.SP_DialogApplyButton)
        )
        self.clear_visible_button = QPushButton("Clear Visible", self)
        self.clear_visible_button.setObjectName("clearVisible")
        self.clear_visible_button.setIcon(
            self.style().standardIcon(QStyle.SP_DialogResetButton)
        )
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.Apply | QDialogButtonBox.Cancel,
            Qt.Horizontal,
            self,
        )
        self.apply_button = self.button_box.button(QDialogButtonBox.Apply)
        self.apply_button.setObjectName("applySelection")
        self.cancel_button = self.button_box.button(QDialogButtonBox.Cancel)
        self.cancel_button.setObjectName("cancelSelection")
        self.apply_button.setDefault(True)

        action_layout = QHBoxLayout()
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(6)
        action_layout.addWidget(self.select_visible_button)
        action_layout.addWidget(self.clear_visible_button)
        action_layout.addStretch(1)
        action_layout.addWidget(self.button_box)

        layout = QVBoxLayout()
        self.content_layout().addLayout(layout)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(self.search)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.status)
        layout.addLayout(action_layout)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._wheel_filter is None:
            self._wheel_filter = WheelForwardingFilter(self.tree, self)
        self._wheel_filter.install()

    def hideEvent(self, event) -> None:
        if self._wheel_filter is not None:
            self._wheel_filter.remove()
        super().hideEvent(event)

    def _connect_ui(self) -> None:
        self.search.textChanged.connect(lambda _text: self._filter_timer.start())
        self._filter_timer.timeout.connect(self._apply_filter)
        self.model.selectionChanged.connect(self._update_status)
        self.select_visible_button.clicked.connect(
            lambda: self.set_visible_selected(True)
        )
        self.clear_visible_button.clicked.connect(
            lambda: self.set_visible_selected(False)
        )
        self.button_box.clicked.connect(self._button_clicked)

    def _button_clicked(self, button) -> None:
        role = self.button_box.buttonRole(button)
        if role == QDialogButtonBox.ApplyRole:
            self.apply_selection()
        elif role == QDialogButtonBox.RejectRole:
            self.reject()

    def _apply_filter(self) -> None:
        self.proxy.set_query(self.search.text())
        if self.search.text().strip():
            self.tree.expandAll()
        self._update_status()

    def set_filter(self, text: str) -> None:
        blocker = QSignalBlocker(self.search)
        self.search.setText(text)
        del blocker
        self._filter_timer.stop()
        self._apply_filter()

    def _visible_records(self) -> tuple[list[str], list[str]]:
        checks: list[str] = []
        opaque_groups: list[str] = []
        for group_row in range(self.proxy.rowCount()):
            proxy_group = self.proxy.index(group_row, 0)
            source_group = self.proxy.mapToSource(proxy_group)
            group = str(source_group.data(NAME_ROLE))
            members = self.model.members_for_group(group)
            if not members:
                opaque_groups.append(group)
                continue
            for child_row in range(self.proxy.rowCount(proxy_group)):
                proxy_child = self.proxy.index(child_row, 0, proxy_group)
                source_child = self.proxy.mapToSource(proxy_child)
                checks.append(str(source_child.data(NAME_ROLE)))
        return checks, opaque_groups

    def set_visible_selected(self, selected: bool) -> None:
        checks, opaque_groups = self._visible_records()
        self.model.set_selected(
            checks=checks, opaque_groups=opaque_groups, selected=selected
        )

    def _update_status(self) -> None:
        selection = self.model.selection()
        source = self.model.info.source
        if self.model.info.cached:
            source += ", cached"
        self.status.setText(
            f"{self.proxy.rowCount()} of {len(self.model.info.groups)} groups shown; "
            f"{len(selection.groups)} groups, {len(selection.checks)} checks "
            f"selected; source: {source}"
        )
        self.apply_button.setEnabled(True)

    def apply_selection(self) -> bool:
        selection = self.model.selection()
        if not selection:
            notice(self, "DRC Rule Select",
                   "Select at least one rule group or check before applying.")
            return False
        try:
            write_selection(self.output_path, selection)
        except OSError as exc:
            notice(
                self,
                "DRC Rule Select",
                f"Cannot write the Rule Select result: {exc}",
            )
            return False
        self.applied = True
        self.accept()
        return True

    def closeEvent(self, event: QCloseEvent) -> None:
        event.accept()
