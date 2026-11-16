"""@ picker scope: selection first, otherwise all window instances/nets/pins."""

from __future__ import annotations

import json
from concurrent.futures import Future
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt5.QtWidgets")

from PyQt5.QtGui import QTextCursor
from PyQt5.QtWidgets import QApplication, QWidget
from sico_ui.ask_objects import AskObjectWindow
from sico_ui.input import SendOnReturnEdit
from sico_ui.object_mention_rows import object_rows
from sico_ui.object_mentions import ObjectMentionController

TARGET = {
    "library": "worklib", "cell": "OTA", "view_name": "schematic",
    "oa_view_type": "schematic", "oa_view_type_source": "platform_api",
}
INSTANCE = {
    "kind": "instance", "name": "NM1",
    "master": {"library": "smic18", "cell": "nmos4", "view_name": "symbol"},
    "hierarchy": [],
}


def selection(items=None, count=None, unsupported=0):
    items = [] if items is None else items
    return {
        "ok": True, "valid": True, "schema": "cad.cv.selection.v1",
        "session_ref": "fixture", "window_ref": "wctx_fixture_1",
        "target": TARGET, "items": items,
        "selection_count": len(items) if count is None else count,
        "unsupported_count": unsupported, "max_items": 50, "offset": 0,
        "truncated": False,
    }


def objects(items):
    return {
        "ok": True, "valid": True, "schema": "cad.cv.objects.v1",
        "session_ref": "fixture", "window_ref": "wctx_fixture_1",
        "target": TARGET, "items": items, "total": len(items),
        "max_items": 50, "offset": 0, "truncated": False,
        "selection_count": 0,
    }


class Commands:
    def watch(self, future, *, success=None, failure=None, settled=None, scope=None):
        try:
            result = future.result()
        except (ValueError, RuntimeError) as exc:
            if failure is not None:
                failure(exc)
            return
        if success is not None:
            success(result)
        if settled is not None:
            settled(result, None)


class Provider:
    def __init__(self, selected, listed=None):
        self.selected = selected
        self.listed = listed
        self.object_calls = []
        self.closed = 0

    def _future(self, value):
        future = Future()
        if isinstance(value, Exception):
            future.set_exception(value)
        else:
            future.set_result(value)
        return future

    def selection(self, window_ref, offset=0, max_items=50):
        return self._future(self.selected)

    def objects(self, window_ref, kind="all", query="", offset=0, max_items=50):
        self.object_calls.append((window_ref, kind, query, offset))
        return self._future(self.listed)

    def close(self):
        self.closed += 1


def make_window(provider):
    window = QWidget()
    window.input = SendOnReturnEdit(window)
    window.presentation = SimpleNamespace(
        submission_context=SimpleNamespace(snapshot={
            "window_ref": "wctx_fixture_1",
            "cellview": {
                "lib": "worklib", "cell": "OTA", "view_name": "schematic",
                "oa_view_type": "schematic",
                "oa_view_type_source": "platform_api",
            },
        })
    )
    window.actions = SimpleNamespace(
        target_picker_provider=lambda: provider,
        capture=lambda: "scope",
    )
    window.commands = Commands()
    window.lifecycle = SimpleNamespace(closing=False)
    window.show()
    return window


def open_picker(window):
    controller = ObjectMentionController(window)
    window.input.setPlainText("@")
    window.input.moveCursor(QTextCursor.End)
    controller.open_at_cursor()
    return controller


def test_selection_scope_does_not_request_window_objects():
    provider = Provider(selection([INSTANCE]))
    window = make_window(provider)
    try:
        controller = open_picker(window)
        assert controller._scope == "selection"
        assert controller.popup.rows.count() == 1
        assert "NM1" in controller.popup.rows.item(0).text()
        assert provider.object_calls == []
    finally:
        window.close()


def test_unsupported_selection_does_not_fall_back_to_window_objects():
    provider = Provider(selection([], count=2, unsupported=2))
    window = make_window(provider)
    try:
        controller = open_picker(window)
        assert controller._scope == "selection"
        assert "暂不支持" in controller.popup.rows.item(0).text()
        assert "清空选择" in controller.popup.rows.item(0).text()
        assert provider.object_calls == []
        controller.popup.rows.setCurrentRow(0)
        controller.choose()
        assert not controller.popup.isVisible()
    finally:
        window.close()


def test_selection_failure_does_not_fall_back_to_window_objects():
    provider = Provider(ValueError("窗口已变化"))
    window = make_window(provider)
    try:
        controller = open_picker(window)
        assert controller._scope == "error"
        assert "当前选择读取失败" in controller.popup.rows.item(0).text()
        assert provider.object_calls == []
    finally:
        window.close()


def test_loading_popup_dismisses_instead_of_trapping_enter():
    window = make_window(Provider(selection([])))
    try:
        controller = ObjectMentionController(window)
        controller._trigger_start = 0
        controller.popup.show_loading()
        assert controller.popup.isVisible()
        controller.choose()
        assert not controller.popup.isVisible()
    finally:
        window.close()


def test_no_selection_lists_instances_nets_and_pins():
    listed = [
        INSTANCE,
        {"kind": "net", "name": "VOUT", "hierarchy": []},
        {"kind": "terminal", "name": "IN", "hierarchy": []},
    ]
    provider = Provider(selection([]), objects(listed))
    window = make_window(provider)
    try:
        controller = open_picker(window)
        text = " ".join(
            controller.popup.rows.item(index).text()
            for index in range(controller.popup.rows.count())
        )
        assert "NM1" in text and "器件" in text
        assert "VOUT" in text and "网络" in text
        assert "IN" in text and "引脚" in text
        assert provider.object_calls == [
            ("wctx_fixture_1", "all", "", 0),
        ]
    finally:
        window.close()


def test_popup_stays_inside_a_short_composer_with_one_full_row():
    application = QApplication.instance() or QApplication([])
    parent = QWidget()
    parent.resize(360, 150)
    parent.move(80, 380)
    input_widget = SendOnReturnEdit(parent)
    input_widget.setGeometry(10, 90, 300, 40)
    parent.input = input_widget
    parent.show()
    application.processEvents()
    try:
        controller = ObjectMentionController(parent)
        controller.popup.set_items(object_rows(TARGET, [INSTANCE], "当前窗口"))
        application.processEvents()
        popup = controller.popup
        assert popup.isWindow() is False
        assert parent.rect().contains(popup.geometry())
        row_height = popup.rows.sizeHintForRow(0)
        assert popup.rows.viewport().height() >= row_height
    finally:
        controller.close()
        parent.close()


def test_ask_queries_recapture_the_bound_window_instead_of_using_a_stale_ref():
    application = QApplication.instance() or QApplication([])
    parent = QWidget()
    input_widget = SendOnReturnEdit(parent)
    parent.input = input_widget
    parent.show()
    requests = []
    ask = AskObjectWindow(
        parent, input_widget, {"window_ref": "wctx_stale_9"}, requests.append)
    controller = ObjectMentionController(ask)
    input_widget.setPlainText("@")
    input_widget.moveCursor(QTextCursor.End)
    controller.open_at_cursor()
    application.processEvents()
    try:
        assert controller._window_ref == "-"
        assert requests and requests[0].split(" ")[2] == "-"
    finally:
        ask.close()
        parent.close()


def test_ask_without_context_still_queries_the_bound_window():
    application = QApplication.instance() or QApplication([])
    parent = QWidget()
    input_widget = SendOnReturnEdit(parent)
    parent.input = input_widget
    parent.show()
    requests = []
    ask = AskObjectWindow(parent, input_widget, {}, requests.append)
    controller = ObjectMentionController(ask)
    input_widget.setPlainText("@")
    input_widget.moveCursor(QTextCursor.End)
    controller.open_at_cursor()
    application.processEvents()
    try:
        assert requests and requests[0].split(" ")[2] == "-"
    finally:
        ask.close()
        parent.close()


def test_ask_lazy_capture_keeps_the_resolved_window_on_inserted_refs():
    application = QApplication.instance() or QApplication([])
    parent = QWidget()
    input_widget = SendOnReturnEdit(parent)
    parent.input = input_widget
    parent.show()
    requests = []
    ask = AskObjectWindow(
        parent, input_widget,
        {"cellview": {
            "lib": "worklib", "cell": "OTA", "view_name": "schematic",
            "oa_view_type": "schematic",
            "oa_view_type_source": "platform_api",
         }, "window": "window:2",
         "title": "Schematic Editor"},
        requests.append,
    )
    controller = ObjectMentionController(ask)
    input_widget.setPlainText("@")
    input_widget.moveCursor(QTextCursor.End)
    controller.open_at_cursor()
    application.processEvents()

    def respond(request, value):
        fields = request.split(" ")
        ask.receive(json.dumps({"id": fields[1], "value": value}))

    try:
        assert requests[0].split(" ")[2] == "-"
        respond(requests[0], {**selection([]), "window_ref": "wctx_real_1"})
        for request in requests[1:]:
            if request.startswith("objects "):
                fields = request.split(" ")
                assert len(fields) == 7 and fields[6] == "-"
                respond(request, {**objects([INSTANCE]),
                                  "window_ref": "wctx_real_1"})
            elif request.startswith("windows "):
                respond(request, {
                    "ok": True, "valid": True, "schema": "cad.windows.v1",
                    "session_ref": "fixture", "pid": 123, "windows": [],
                    "max_items": 200, "total": 0, "offset": 0,
                    "next_offset": None, "truncated": False,
                    "snapshot_atomic": False,
                    "read_scope": "current Virtuoso process only",
                })
        labels = [
            controller.popup.rows.item(index).text()
            for index in range(controller.popup.rows.count())
        ]
        row = next(index for index, label in enumerate(labels) if "NM1" in label)
        controller.popup.rows.setCurrentRow(row)
        controller.choose()
        assert input_widget.object_inputs()[0]["window_ref"] == "wctx_real_1"
    finally:
        ask.close()
        parent.close()


def test_ask_capability_failure_names_the_loader_command():
    application = QApplication.instance() or QApplication([])
    parent = QWidget()
    input_widget = SendOnReturnEdit(parent)
    parent.input = input_widget
    requests = []
    ask = AskObjectWindow(
        parent, input_widget, {"window_ref": "wctx_fixture_1"},
        requests.append)
    controller = ObjectMentionController(ask)
    input_widget.setPlainText("@")
    input_widget.moveCursor(QTextCursor.End)
    controller.open_at_cursor()
    application.processEvents()
    try:
        request_id = requests[0].split(" ")[1]
        ask.receive(json.dumps({
            "id": request_id,
            "value": {"ok": False, "code": "capability_not_available"},
        }))
        assert "SICO.ils" in controller.popup.rows.item(0).text()
    finally:
        ask.close()
        parent.close()


def test_ask_picker_reopens_after_the_previous_popup_closed():
    application = QApplication.instance() or QApplication([])
    parent = QWidget()
    input_widget = SendOnReturnEdit(parent)
    parent.input = input_widget
    parent.show()
    requests = []
    ask = AskObjectWindow(
        parent, input_widget,
        {"cellview": {
            "lib": "worklib", "cell": "OTA", "view_name": "schematic",
            "oa_view_type": "schematic",
            "oa_view_type_source": "platform_api",
         }},
        requests.append,
    )
    controller = ObjectMentionController(ask)
    try:
        input_widget.setPlainText("@")
        input_widget.moveCursor(QTextCursor.End)
        controller.open_at_cursor()
        assert len(requests) == 1
        controller.close()

        input_widget.setPlainText("@")
        input_widget.moveCursor(QTextCursor.End)
        controller.open_at_cursor()
        application.processEvents()
        assert len(requests) == 2
        assert requests[1].startswith("selection ")
        assert all(
            "已关闭" not in controller.popup.rows.item(index).text()
            for index in range(controller.popup.rows.count())
        )
    finally:
        ask.close()
        parent.close()
