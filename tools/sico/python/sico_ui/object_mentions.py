"""@ object picker for the main conversation composer."""

from __future__ import annotations

from PyQt5.QtCore import QEvent, QObject, Qt, QTimer
from PyQt5.QtWidgets import QApplication

from sico.core.cad_ref import object_ref, target_from_snapshot
from .object_mention_popup import ObjectMentionPopup

QUERY_CHARS = 64


class ObjectMentionController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.input = window.input
        self.popup = ObjectMentionPopup(getattr(window, "popup_parent", window))
        self._generation = 0
        self._trigger_start = None
        self._selection_items = []
        self._object_items = []
        self._window_items = []
        self._other_windows = []
        self._target = None
        self._window_label = ""
        self._object_offset = 0
        self._object_total = 0
        self._object_truncated = False
        self._loading_more = False
        self._provider = None
        self._window_ref = ""
        self._query = ""
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self._search_objects)
        self.input.installEventFilter(self)
        self.input.textChanged.connect(self._text_changed)
        self.popup.rows.itemClicked.connect(lambda _item: self.choose())
        self.popup.rows.itemActivated.connect(lambda _item: self.choose())
        self.popup.loadMoreRequested.connect(self._load_more)
        application = QApplication.instance()
        if application is not None:
            application.focusChanged.connect(self._focus_changed)
        window.destroyed.connect(lambda *_args: self.close())

    def eventFilter(self, watched, event):
        if watched is not self.input:
            return False
        if event.type() == QEvent.KeyPress and self.popup.isVisible():
            key = event.key()
            if key == Qt.Key_Escape:
                self.close()
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab):
                self.choose()
                return True
            if key == Qt.Key_Up:
                self.popup.move_selection(-1)
                return True
            if key == Qt.Key_Down:
                self.popup.move_selection(1)
                return True
        if event.type() == QEvent.KeyPress and event.text() == "@":
            QTimer.singleShot(0, self._maybe_open)
        return False

    def _maybe_open(self):
        if not self.popup.isVisible() and self.input.hasFocus():
            self.open_at_cursor()

    def open_at_cursor(self):
        page = getattr(self.window, "page", None)
        if page is not None and (page.reviewing is not None or page.opening):
            return
        if self.input.isReadOnly() or self.window.lifecycle.closing:
            return
        cursor = self.input.textCursor()
        text = self.input.toPlainText()
        position = cursor.position()
        start = text.rfind("@", 0, position)
        if start < 0:
            return
        query = text[start + 1:position]
        if len(query) > QUERY_CHARS or any(char.isspace() for char in query):
            return
        if start and text[start - 1] not in " \t\n([{，。；：":
            return
        self._trigger_start = start
        self._query = query
        self._load()

    def _text_changed(self):
        if not self.popup.isVisible():
            return
        cursor = self.input.textCursor()
        text = self.input.toPlainText()
        position = cursor.position()
        if self._trigger_start is None or position <= self._trigger_start:
            self.close()
            return
        query = text[self._trigger_start + 1:position]
        if len(query) > QUERY_CHARS or any(char.isspace() for char in query):
            self.close()
            return
        self._query = query
        self._filter()
        self._search_timer.start()

    def _context(self):
        context = self.window.presentation.submission_context
        return (context.snapshot or {}) if context is not None else {}

    def _load(self):
        window_ref = self._context().get("window_ref", "")
        if not window_ref:
            self.popup.show_error("当前会话没有捕获窗口")
            return
        try:
            provider = self.window.actions.target_picker_provider()
        except (ValueError, RuntimeError) as exc:
            self.popup.show_error(str(exc))
            return
        if provider is None:
            self.popup.show_error("当前会话没有桥接连接")
            return
        self._provider = provider
        self._window_ref = window_ref
        target = target_from_snapshot(self._context())
        self._target = target
        self._window_label = self._make_window_label(
            target, self._context().get("window"), self._context().get("title")
        ) if target else window_ref
        self._window_items = self._window_rows()
        self._generation += 1
        generation = self._generation
        self.popup.show_loading()
        self._selection_items = []
        self._object_items = []
        self._object_offset = 0
        self._object_total = 0
        self._object_truncated = False
        self._loading_more = False
        scope = self.window.actions.capture()
        self._load_selection(generation, window_ref, scope)
        self._load_objects(generation, window_ref, self._query, scope, 0, False)
        self._load_window_choices(generation, scope)

    def _load_selection(self, generation, window_ref, scope):
        try:
            future = self._provider.selection(window_ref, 0, 50)
        except (ValueError, RuntimeError):
            self._selection_items = []
            self._filter()
            return

        def loaded(selection):
            if generation != self._generation or self.window.lifecycle.closing:
                return
            self._selection_items = self._rows(
                selection["target"], selection["items"], "当前选择",
                self._window_ref, self._window_label,
            )
            self._filter()

        def failed(exc):
            if generation == self._generation:
                self._selection_items = []
                self._filter()
                if not self._object_items:
                    self.popup.show_error("当前选择读取失败；草稿保留")

        self.window.commands.watch(
            future, success=loaded, failure=failed, scope=scope
        )

    def _load_objects(self, generation, window_ref, query, scope, offset, append):
        self._loading_more = True
        try:
            future = self._provider.objects(window_ref, "all", query, offset, 50)
        except (ValueError, RuntimeError):
            if not append:
                self._object_items = []
            self._loading_more = False
            self._filter()
            return

        def loaded(result):
            if generation != self._generation or self.window.lifecycle.closing:
                return
            rows = self._rows(
                result["target"], result["items"], "当前窗口",
                self._window_ref, self._window_label,
            )
            if append:
                self._object_items.extend(rows)
            else:
                self._object_items = rows
            self._object_offset = result["offset"] + len(result["items"])
            self._object_total = result["total"]
            self._object_truncated = result["truncated"]
            self._loading_more = False
            self._filter()

        def failed(exc):
            if generation == self._generation:
                if not append:
                    self._object_items = []
                self._loading_more = False
                self._filter()
                if not self._selection_items:
                    self.popup.show_error("当前窗口对象读取失败；草稿保留")

        self.window.commands.watch(future, success=loaded, failure=failed, scope=scope)

    def _load_window_choices(self, generation, scope):
        loader = getattr(self._provider, "window_list", None)
        if loader is None:
            return
        try:
            future = loader()
        except (ValueError, RuntimeError):
            return

        def loaded(result):
            if generation != self._generation or self.window.lifecycle.closing:
                return
            self._other_windows = [
                row for row in result.get("windows", [])
                if row.get("window_ref") != self._window_ref
            ]
            self._window_items = self._window_rows()
            self._filter()

        self.window.commands.watch(
            future, success=loaded, failure=lambda _exc: None, scope=scope
        )

    @staticmethod
    def _make_window_label(target, window=None, title=None):
        if not isinstance(target, dict):
            return str(window or "")
        label = "/".join((target["library"], target["cell"], target["view"]))
        if window:
            label += " · " + str(window)
        if isinstance(title, str) and title:
            clean = " ".join(title.split())
            if clean:
                label += " · " + clean[:80]
        return label[:256]

    def _window_rows(self):
        if not isinstance(self._target, dict):
            return []
        label = self._window_label or self._make_window_label(self._target)
        name = "/".join((
            self._target["library"], self._target["cell"], self._target["view"],
        ))
        rows = [{
            "label": label,
            "detail": "当前窗口 · cellview",
            "window_ref": self._window_ref,
            "window_label": label,
            "ref": object_ref(self._target, {
                "kind": "cellview", "name": name, "hierarchy": [],
            }, require_master=False),
        }]
        for window in self._other_windows:
            target = window.get("target")
            if not isinstance(target, dict) or window.get("window_ref") == self._window_ref:
                continue
            window_label = self._make_window_label(
                target, window.get("window"), window.get("title")
            )
            rows.append({
                "label": window_label,
                "detail": "其他窗口 · cellview",
                "window_ref": window["window_ref"],
                "window_label": window_label,
                "switch_window": window,
            })
        return rows

    @staticmethod
    def _rows(target, items, source, window_ref=None, window_label=None):
        rows = []
        for item in items:
            detail = (window_label or source) + " · " + item["kind"]
            if item.get("master"):
                master = item["master"]
                detail += " · " + "/".join((master["library"], master["cell"], master["view"]))
            row = {
                "label": item["name"],
                "detail": detail,
                "ref": object_ref(target, item),
                "window_ref": window_ref,
                "window_label": window_label,
            }
            rows.append(row)
        return rows

    def _search_objects(self):
        if not self.popup.isVisible() or self._provider is None:
            return
        self._object_offset = 0
        self._object_truncated = False
        self._load_objects(
            self._generation, self._window_ref, self._query,
            self.window.actions.capture(), 0, False,
        )

    def _load_more(self):
        if (not self.popup.isVisible() or self._provider is None
                or self._loading_more or not self._object_truncated):
            return
        self._load_objects(
            self._generation, self._window_ref, self._query,
            self.window.actions.capture(), self._object_offset, True,
        )

    def _filter(self):
        query = self._query.casefold()
        rows, seen = [], set()
        for row in [*self._window_items, *self._selection_items, *self._object_items]:
            if "ref" not in row:
                if not query or query in row["label"].casefold() or query in row["detail"].casefold():
                    rows.append(row)
                continue
            ref = row["ref"]
            master = ref.get("master")
            key = (
                row.get("window_ref"),
                ref["kind"],
                tuple(ref["hierarchy"]),
                ref["name"],
                tuple(master[key] for key in ("library", "cell", "view")) if master else None,
            )
            if key in seen:
                continue
            seen.add(key)
            if not query or query in row["label"].casefold() or query in row["detail"].casefold():
                rows.append(row)
        scroll = self.popup.rows.verticalScrollBar().value()
        self.popup.set_items(rows)
        self.popup.rows.verticalScrollBar().setValue(scroll)
        if self._object_truncated:
            self.popup.set_hint(
                f"↑/↓ 选择，Enter 插入，Esc 关闭；已加载 {len(self._object_items)} / "
                f"{self._object_total}，滚动加载更多"
            )

    def choose(self):
        payload = self.popup.current_payload()
        if not payload or self._trigger_start is None:
            return
        if payload.get("switch_window") is not None:
            window = payload["switch_window"]
            self._window_ref = window["window_ref"]
            self._window_label = payload["window_label"]
            self._target = window.get("target")
            self._selection_items = []
            self._object_items = []
            self._object_offset = 0
            self._object_total = 0
            self._object_truncated = False
            self._window_items = self._window_rows()
            self.popup.show_loading("正在读取该窗口对象…")
            scope = self.window.actions.capture()
            self._load_selection(self._generation, self._window_ref, scope)
            self._load_objects(
                self._generation, self._window_ref, self._query, scope, 0, False
            )
            return
        cursor = self.input.textCursor()
        end = cursor.position()
        if end <= self._trigger_start:
            self.close()
            return
        cursor.setPosition(self._trigger_start)
        cursor.setPosition(end, cursor.KeepAnchor)
        cursor.removeSelectedText()
        self.input.setTextCursor(cursor)
        self.input.insert_object_ref(
            payload["ref"], payload["label"],
            window_ref=payload.get("window_ref"),
            window_label=payload.get("window_label"),
        )
        self.input.setFocus()
        self.close()

    def _focus_changed(self, _old, new):
        if not self.popup.isVisible():
            return
        if new is self.input or (new is not None and self.popup.isAncestorOf(new)):
            return
        QTimer.singleShot(150, self._close_if_focus_outside)

    def _close_if_focus_outside(self):
        focus = QApplication.focusWidget()
        if focus is not self.input and not (
            focus is not None and self.popup.isAncestorOf(focus)
        ):
            self.close()

    def close(self):
        self._generation += 1
        self._search_timer.stop()
        if self._provider is not None:
            self._provider.close()
            self._provider = None
        self.popup.hide()
        self._trigger_start = None
        self._query = ""
        self._selection_items = []
        self._object_items = []
        self._window_items = []
        self._other_windows = []
        self._target = None
        self._window_label = ""
        self._object_offset = 0
        self._object_total = 0
        self._object_truncated = False
        self._loading_more = False


__all__ = ["ObjectMentionController", "ObjectMentionPopup"]
