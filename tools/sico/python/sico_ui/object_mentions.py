"""@ object picker for the main conversation composer."""

from __future__ import annotations

from PyQt5.QtCore import QEvent, QObject, Qt, QTimer
from PyQt5.QtWidgets import QApplication

from sico.core.cad_ref import target_from_snapshot
from .object_mention_rows import object_rows, window_label, window_rows
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
        self._object_request = 0
        self._scope = "idle"
        self._selection_count = 0
        self._selection_unsupported = 0
        self._notice = None
        self._notice_detail = ""
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
        if watched is not getattr(self, "input", None):
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
        context = self._context()
        window_ref = context.get("window_ref", "")
        if not window_ref:
            # The Ask open envelope carries its own capture, so a context that
            # failed to repeat the reference can still use that owner window.
            window_ref = getattr(self.window, "window_ref", "")
        if not window_ref:
            self.popup.show_error("Ask 未能捕获当前窗口；请重新打开 Ask")
            return
        try:
            loader = getattr(self.window.actions, "object_picker_provider", None)
            provider = (
                loader() if loader is not None
                else self.window.actions.target_picker_provider()
            )
        except (ValueError, RuntimeError) as exc:
            self.popup.show_error(str(exc))
            return
        if provider is None:
            self.popup.show_error("当前会话没有桥接连接")
            return
        self._provider = provider
        self._window_ref = window_ref
        target = target_from_snapshot(context)
        self._target = target
        self._window_label = window_label(
            target, context.get("window"), context.get("title")
        ) if target else window_ref
        self._window_items = []
        self._generation += 1
        generation = self._generation
        self._scope = "loading"
        self._selection_count = 0
        self._selection_unsupported = 0
        self._notice = None
        self._notice_detail = ""
        self.popup.show_loading("正在读取当前选择…")
        self._selection_items = []
        self._clear_object_page()
        scope = self.window.actions.capture()
        self._load_selection(generation, window_ref, scope)

    def _load_selection(self, generation, window_ref, scope):
        try:
            future = self._provider.selection(window_ref, 0, 50)
        except (ValueError, RuntimeError) as exc:
            self._scope = "error"
            self._selection_items = []
            self._filter()
            message = str(exc).strip() or "未知错误"
            self.popup.show_error(
                "当前选择读取失败：" + message[:160] + "；草稿保留")
            return

        def loaded(selection):
            if generation != self._generation or self.window.lifecycle.closing:
                return
            self._adopt_response(selection)
            items = selection.get("items") or []
            count = selection.get("selection_count")
            if type(count) is not int:
                count = len(items)
            unsupported = selection.get("unsupported_count")
            if type(unsupported) is not int:
                unsupported = 0
            self._selection_count = count
            self._selection_unsupported = unsupported
            self._selection_items = object_rows(
                selection["target"], items, "当前选择",
                self._window_ref, self._window_label,
            )
            if count > 0:
                self._enter_selection_scope(generation, scope)
            else:
                self._enter_window_scope(generation, window_ref, scope)

        def failed(exc):
            if generation == self._generation:
                self._scope = "error"
                self._selection_items = []
                self._object_items = []
                self._window_items = []
                self._loading_more = False
                self._filter()
                message = str(exc).strip() or "未知错误"
                self.popup.show_error(
                    "当前选择读取失败：" + message[:160] + "；草稿保留")

        self.window.commands.watch(
            future, success=loaded, failure=failed, scope=scope
        )

    def _enter_selection_scope(self, generation, scope):
        self._scope = "selection"
        self._clear_object_page()
        self._notice = None
        self._notice_detail = ""
        self._window_items = window_rows(
            self._target, self._window_ref, self._window_label,
            self._other_windows, include_current=False)
        if self._selection_items:
            self._filter()
        else:
            count = self._selection_unsupported or self._selection_count
            self._notice = (
                f"当前选择包含 {count} 个暂不支持的对象" if count
                else "当前选择包含暂不支持的对象")
            self._notice_detail = "清空选择后可浏览当前窗口的器件、网络和引脚"
            self._filter()
        self._load_window_choices(generation, scope)

    def _enter_window_scope(self, generation, window_ref, scope):
        self._scope = "window"
        self._clear_object_page()
        self._notice = None
        self._notice_detail = ""
        self._selection_items = []
        self._window_items = window_rows(
            self._target, self._window_ref, self._window_label, self._other_windows)
        self._filter()
        self._load_objects(
            generation, self._window_ref, self._query, scope, 0, False,
            request=self._object_request,
        )
        self._load_window_choices(generation, scope)

    def _load_objects(self, generation, window_ref, query, scope, offset, append,
                      request=None):
        request = self._object_request if request is None else request
        if request != self._object_request:
            return
        self._loading_more = True
        try:
            future = self._provider.objects(window_ref, "all", query, offset, 50)
        except (ValueError, RuntimeError) as exc:
            if not append and request == self._object_request:
                self._object_items = []
            self._loading_more = False
            self._filter()
            message = str(exc).strip() or "未知错误"
            if self._window_items:
                self.popup.set_hint("当前窗口对象读取失败：" + message[:100])
            else:
                self.popup.show_error(
                    "当前窗口对象读取失败：" + message[:160] + "；草稿保留")
            return

        def loaded(result):
            if (request != self._object_request
                    or generation != self._generation
                    or self.window.lifecycle.closing):
                return
            self._adopt_response(result)
            if self._scope == "window":
                self._window_items = window_rows(
                    self._target, self._window_ref, self._window_label,
                    self._other_windows)
            rows = object_rows(
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
            if request == self._object_request and generation == self._generation:
                if not append:
                    self._object_items = []
                self._loading_more = False
                self._filter()
                message = str(exc).strip() or "未知错误"
                if self._window_items:
                    self.popup.set_hint("当前窗口对象读取失败：" + message[:100])
                else:
                    self.popup.show_error(
                        "当前窗口对象读取失败：" + message[:160] + "；草稿保留")

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
            if self._scope not in ("window", "selection"):
                return
            self._other_windows = [
                row for row in result.get("windows", [])
                if row.get("window_ref") != self._window_ref
            ]
            self._window_items = window_rows(
                self._target, self._window_ref, self._window_label,
                self._other_windows, include_current=self._scope == "window")
            self._filter()

        self.window.commands.watch(
            future, success=loaded, failure=lambda _exc: None, scope=scope
        )

    def _adopt_response(self, response):
        """Adopt a lazily resolved window ref and a partial-context target."""
        ref = response.get("window_ref")
        if isinstance(ref, str) and ref.startswith("wctx_"):
            self._window_ref = ref
        target = response.get("target")
        if isinstance(target, dict) and not isinstance(self._target, dict):
            self._target = target
            context = self._context()
            self._window_label = window_label(
                target, context.get("window"), context.get("title"))

    def _clear_object_page(self):
        self._object_items = []
        self._object_offset = 0
        self._object_total = 0
        self._object_truncated = False
        self._loading_more = False
        self._object_request += 1

    def _search_objects(self):
        if (not self.popup.isVisible() or self._provider is None
                or self._scope != "window"):
            return
        self._object_offset = 0
        self._object_truncated = False
        self._object_request += 1
        self._load_objects(
            self._generation, self._window_ref, self._query,
            self.window.actions.capture(), 0, False,
            request=self._object_request,
        )

    def _load_more(self):
        if (not self.popup.isVisible() or self._provider is None
                or self._loading_more or not self._object_truncated
                or self._scope != "window"):
            return
        self._load_objects(
            self._generation, self._window_ref, self._query,
            self.window.actions.capture(), self._object_offset, True,
            request=self._object_request,
        )

    def _filter(self):
        query = self._query.casefold()
        rows, seen = [], set()
        if self._notice:
            rows.append({"label": self._notice, "detail": self._notice_detail})
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
                tuple(master[key] for key in ("library", "cell", "view_name")) if master else None,
            )
            if key in seen:
                continue
            seen.add(key)
            if not query or query in row["label"].casefold() or query in row["detail"].casefold():
                rows.append(row)
        scroll = self.popup.rows.verticalScrollBar().value()
        self.popup.set_items(rows)
        self.popup.rows.verticalScrollBar().setValue(scroll)
        self.popup.show_scope(
            self._scope, count=len(rows),
            selection_count=self._selection_count,
            object_count=len(self._object_items),
            object_total=self._object_total,
            truncated=self._object_truncated, query=self._query,
        )

    def choose(self):
        payload = self.popup.current_payload()
        if self._trigger_start is None:
            return
        if not payload:
            self.close()
            return
        if payload.get("switch_window") is not None:
            window = payload["switch_window"]
            self._window_ref = window["window_ref"]
            self._window_label = payload["window_label"]
            self._target = window.get("target")
            self._scope = "loading"
            self._selection_count = 0
            self._selection_unsupported = 0
            self._selection_items = []
            self._clear_object_page()
            self._window_items = []
            self._notice = None
            self._notice_detail = ""
            self.popup.show_loading("正在读取该窗口选择…")
            scope = self.window.actions.capture()
            self._load_selection(self._generation, self._window_ref, scope)
            return
        if "ref" not in payload:
            # Loading/notice rows are not insertable. Dismiss the popup so
            # Enter/Tab cannot trap keyboard focus on a non-actionable row.
            self.close()
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
        self._scope = "idle"
        self._selection_count = 0
        self._selection_unsupported = 0
        self._notice = None
        self._notice_detail = ""
        self._clear_object_page()


__all__ = ["ObjectMentionController", "ObjectMentionPopup"]
