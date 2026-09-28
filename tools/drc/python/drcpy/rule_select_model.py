"""Qt tree projection of the canonical selected checks and opaque groups."""

from __future__ import annotations

from typing import Iterable
from .rule_groups import RuleGroupInfo
from .rule_selection import RuleSelection
from .rule_select_qt import QFont, QStandardItem, QStandardItemModel, Qt, pyqtSignal


_KIND_ROLE = Qt.UserRole + 1
NAME_ROLE = Qt.UserRole + 2
_KEY_ROLE = Qt.UserRole + 3
_MEMBERS_ROLE = Qt.UserRole + 4


class RuleSelectModel(QStandardItemModel):
    """Single-build tree model with one shared state per case-insensitive check."""

    selectionChanged = pyqtSignal()

    def __init__(
        self,
        groups: RuleGroupInfo,
        initial: RuleSelection | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.info = groups
        self._updating = False
        self._group_names: dict[str, str] = {}
        self._group_members: dict[str, tuple[str, ...]] = {}
        self._group_items: dict[str, QStandardItem] = {}
        self._check_names: dict[str, str] = {}
        self._check_order: list[str] = []
        self._check_items: dict[str, list[QStandardItem]] = {}
        self._selected_checks: dict[str, str] = {}
        self._selected_opaque_groups: dict[str, str] = {}
        self.setHorizontalHeaderLabels(("Rule Group / Check", "Checks"))
        self._index_groups()
        self._build_items()
        self.itemChanged.connect(self._item_changed)
        self.apply_initial(initial or RuleSelection())

    @staticmethod
    def _unique_members(members: Iterable[str]) -> tuple[str, ...]:
        unique: list[str] = []
        seen: set[str] = set()
        for member in members:
            key = member.casefold()
            if key not in seen:
                seen.add(key)
                unique.append(member)
        return tuple(unique)

    def _index_groups(self) -> None:
        source_members = self.info.members or {}
        for group in self.info.groups:
            group_key = group.casefold()
            self._group_names[group_key] = group
            raw_members = source_members.get(group)
            if raw_members is None:
                raw_members = next(
                    (
                        value
                        for name, value in source_members.items()
                        if name.casefold() == group_key
                    ),
                    (),
                )
            members = self._unique_members(raw_members)
            self._group_members[group_key] = members
            for check in members:
                check_key = check.casefold()
                if check_key not in self._check_names:
                    self._check_names[check_key] = check
                    self._check_order.append(check_key)

    @staticmethod
    def _configured_item(text: str, *, kind: str, key: str) -> QStandardItem:
        item = QStandardItem(text)
        item.setEditable(False)
        item.setCheckable(True)
        item.setData(kind, _KIND_ROLE)
        item.setData(text, NAME_ROLE)
        item.setData(key, _KEY_ROLE)
        return item

    def _build_items(self) -> None:
        for group in self.info.groups:
            group_key = group.casefold()
            members = self._group_members[group_key]
            group_item = self._configured_item(group, kind="group", key=group_key)
            group_item.setData(members, _MEMBERS_ROLE)
            font = QFont(group_item.font())
            font.setBold(True)
            group_item.setFont(font)
            count_item = QStandardItem(str(self.info.counts.get(group, len(members))))
            count_item.setEditable(False)
            count_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.appendRow((group_item, count_item))
            self._group_items[group_key] = group_item
            for check in members:
                check_key = check.casefold()
                check_item = self._configured_item(
                    self._check_names[check_key], kind="check", key=check_key
                )
                check_item.setData(check, NAME_ROLE)
                blank_item = QStandardItem("")
                blank_item.setEditable(False)
                group_item.appendRow((check_item, blank_item))
                self._check_items.setdefault(check_key, []).append(check_item)

    def apply_initial(self, selection: RuleSelection) -> None:
        self._updating = True
        try:
            self._selected_checks.clear()
            self._selected_opaque_groups.clear()
            for group in selection.groups:
                group_key = group.casefold()
                members = self._group_members.get(group_key)
                if members:
                    for check in members:
                        self._select_check(check, True)
                else:
                    canonical = self._group_names.get(group_key, group)
                    self._selected_opaque_groups[group_key] = canonical
            for check in selection.checks:
                self._select_check(check, True)
            self._sync_items()
        finally:
            self._updating = False
        self.selectionChanged.emit()

    def _select_check(self, name: str, selected: bool) -> None:
        key = name.casefold()
        if selected:
            self._selected_checks[key] = self._check_names.get(key, name)
        else:
            self._selected_checks.pop(key, None)

    def _select_opaque_group(self, name: str, selected: bool) -> None:
        key = name.casefold()
        if selected:
            self._selected_opaque_groups[key] = self._group_names.get(key, name)
        else:
            self._selected_opaque_groups.pop(key, None)

    def is_check_selected(self, name: str) -> bool:
        return name.casefold() in self._selected_checks

    def group_state(self, name: str) -> Qt.CheckState:
        key = name.casefold()
        members = self._group_members.get(key, ())
        if not members:
            return Qt.Checked if key in self._selected_opaque_groups else Qt.Unchecked
        selected = sum(self.is_check_selected(check) for check in members)
        if selected == 0:
            return Qt.Unchecked
        if selected == len(members):
            return Qt.Checked
        return Qt.PartiallyChecked

    def _sync_items(self) -> None:
        for key, items in self._check_items.items():
            state = Qt.Checked if key in self._selected_checks else Qt.Unchecked
            for item in items:
                item.setCheckState(state)
        for key, item in self._group_items.items():
            item.setCheckState(self.group_state(key))

    def _item_changed(self, item: QStandardItem) -> None:
        if self._updating:
            return
        kind = item.data(_KIND_ROLE)
        if kind not in {"group", "check"}:
            return
        key = str(item.data(_KEY_ROLE))
        selected = item.checkState() == Qt.Checked
        self._updating = True
        try:
            if kind == "check":
                self._select_check(self._check_names.get(key, item.text()), selected)
            else:
                members = self._group_members.get(key, ())
                if members:
                    for check in members:
                        self._select_check(check, selected)
                else:
                    self._select_opaque_group(
                        self._group_names.get(key, item.text()), selected
                    )
            self._sync_items()
        finally:
            self._updating = False
        self.selectionChanged.emit()

    def set_check_selected(self, name: str, selected: bool) -> None:
        self.set_selected(checks=(name,), selected=selected)

    def set_group_selected(self, name: str, selected: bool) -> None:
        key = name.casefold()
        members = self._group_members.get(key, ())
        if members:
            self.set_selected(checks=members, selected=selected)
        else:
            self.set_selected(opaque_groups=(name,), selected=selected)

    def set_selected(
        self,
        *,
        checks: Iterable[str] = (),
        opaque_groups: Iterable[str] = (),
        selected: bool,
    ) -> None:
        self._updating = True
        try:
            for check in checks:
                self._select_check(check, selected)
            for group in opaque_groups:
                self._select_opaque_group(group, selected)
            self._sync_items()
        finally:
            self._updating = False
        self.selectionChanged.emit()

    def selection(self) -> RuleSelection:
        groups: list[str] = []
        checks: list[str] = []
        covered: set[str] = set()
        emitted_groups: set[str] = set()
        for group in self.info.groups:
            key = group.casefold()
            members = self._group_members[key]
            if self.group_state(group) == Qt.Checked:
                groups.append(group)
                emitted_groups.add(key)
                covered.update(check.casefold() for check in members)
        for key, group in self._selected_opaque_groups.items():
            if key not in emitted_groups:
                groups.append(group)
                emitted_groups.add(key)
        emitted_checks: set[str] = set()
        for key in self._check_order:
            if key in self._selected_checks and key not in covered:
                checks.append(self._check_names[key])
                emitted_checks.add(key)
        for key, check in self._selected_checks.items():
            if key not in covered and key not in emitted_checks:
                checks.append(check)
                emitted_checks.add(key)
        return RuleSelection(tuple(groups), tuple(checks))

    def group_item(self, name: str) -> QStandardItem | None:
        return self._group_items.get(name.casefold())

    def check_items(self, name: str) -> tuple[QStandardItem, ...]:
        return tuple(self._check_items.get(name.casefold(), ()))

    def members_for_group(self, name: str) -> tuple[str, ...]:
        return self._group_members.get(name.casefold(), ())
