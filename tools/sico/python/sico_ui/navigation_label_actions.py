"""Label commands and menus for the session navigation."""

from __future__ import annotations

from PyQt5.QtWidgets import QMenu

from .navigation_labels import (
    KIND_LABEL,
    LABEL_ROLE,
    KIND_ROLE,
)
from .receipts import DataReceipt
from .si_prompt import ask_text, confirm


class LabelActions:
    def _label_state(self):
        return self.catalog_snapshot.get("labels") or {
            "revision": None, "labels": (), "loose_order": (), "error": "",
        }

    def _label_rows(self):
        return list(self._label_state().get("labels", ()))

    def _label_name(self, label_id):
        return next((row["name"] for row in self._label_rows()
                     if row["id"] == label_id), "")

    def _label_for_session(self, session_id):
        return next((row["id"] for row in self._label_rows()
                     if session_id in row.get("sessions", ())), None)

    def _label_receipt(self):
        receipt = getattr(self, "_labels_receipt", None)
        if receipt is None:
            receipt = self._labels_receipt = DataReceipt(self)
        return receipt

    def _label_command(self, action, *, expected_revision=None, **params):
        api = getattr(self.window.api, "navigation_labels", None)
        if not callable(api):
            self._alert("当前窗口未连接标签服务", "标签分类")
            return False
        try:
            future = api(action, expected_revision=expected_revision, **params)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            self._failed("标签操作失败", exc, "")
            return False
        self._label_receipt().watch(
            future,
            lambda _result: self._operation_finished(),
            lambda exc: self._failed("标签操作失败", exc, ""),
        )
        return True

    def _create_label(self):
        name, ok = ask_text(self.window, "新建标签", "名称：")
        if not ok or not name.strip():
            return
        self._label_command(
            "create_label", expected_revision=self._label_state().get("revision"),
            name=name,
        )

    def _rename_label(self, label_id):
        name, ok = ask_text(self.window, "重命名标签", "名称：",
                            value=self._label_name(label_id))
        if not ok or not name.strip():
            return
        self._label_command(
            "rename_label", expected_revision=self._label_state().get("revision"),
            label_id=label_id, name=name,
        )

    def _delete_label(self, label_id):
        name = self._label_name(label_id) or label_id
        if not confirm(
            self.window, "删除标签",
            "删除标签“%s”？会话不会被删除，只会变成未归类。" % name,
            choice="删除标签", danger=True,
        ):
            return
        self._label_command(
            "delete_label", expected_revision=self._label_state().get("revision"),
            label_id=label_id,
        )

    def _move_session_label(self, session_id, label_id):
        self._label_command(
            "move_sessions", expected_revision=self._label_state().get("revision"),
            session_ids=[session_id], label_id=label_id, before_id=None,
        )

    def _session_label_menu(self, menu, session_id):
        submenu = menu.addMenu("移动到标签")
        current = self._label_for_session(session_id)
        for row in self._label_rows():
            action = submenu.addAction(row["name"])
            action.setCheckable(True)
            action.setChecked(row["id"] == current)
            action.triggered.connect(
                lambda _checked=False, label_id=row["id"]:
                self._move_session_label(session_id, label_id)
            )
        action = submenu.addAction("未归类")
        action.setCheckable(True)
        action.setChecked(current is None)
        action.triggered.connect(
            lambda _checked=False: self._move_session_label(session_id, None)
        )

    def _label_menu(self, tree, position, item):
        menu = QMenu(self)
        kind = item.data(0, KIND_ROLE)
        if kind == KIND_LABEL:
            rename = menu.addAction("重命名标签")
            delete = menu.addAction("删除标签")
        else:
            rename = delete = None
        create = menu.addAction("新建标签")
        action = menu.exec_(tree.viewport().mapToGlobal(position))
        if action is None:
            return
        if action is create:
            self._create_label()
        elif rename is not None and action is rename:
            self._rename_label(item.data(0, LABEL_ROLE))
        elif delete is not None and action is delete:
            self._delete_label(item.data(0, LABEL_ROLE))

    def _label_blank_menu(self, tree, position):
        menu = QMenu(self)
        create = menu.addAction("新建标签")
        if menu.exec_(tree.viewport().mapToGlobal(position)) is create:
            self._create_label()

    def _label_drop(self, intent):
        if not isinstance(intent, dict):
            return
        action = intent.get("action")
        params = {key: value for key, value in intent.items() if key != "action"}
        self._label_command(
            action, expected_revision=self._label_state().get("revision"), **params
        )
