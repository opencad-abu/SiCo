"""Composer token identity for CAD object references."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt5.QtWidgets")

from concurrent.futures import Future
from types import SimpleNamespace

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QTextCursor
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QWidget
from sico.core.cad_ref import object_link, object_ref
from sico_ui.cad_objects import CADObjectActions
from sico_ui.input import SendOnReturnEdit
from sico_ui.object_mentions import ObjectMentionController
from sico_ui.turn_input_detail import open_object_input

TARGET = {
    "library": "worklib",
    "cell": "OTA",
    "view_name": "schematic",
    "oa_view_type": "schematic",
    "oa_view_type_source": "platform_api",
}
INSTANCE = {
    "kind": "instance",
    "name": "NM1",
    "master": {"library": "smic18", "cell": "nmos4", "view_name": "symbol"},
    "hierarchy": [],
}


def test_composer_keeps_object_refs_through_token_deletion_and_restore():
    editor = SendOnReturnEdit()
    editor.insertPlainText("change ")
    editor.moveCursor(QTextCursor.End)
    ref = object_ref(TARGET, INSTANCE)
    editor.insert_object_ref(ref, "NM1")
    assert editor.toPlainText().endswith("@NM1")
    expected = [{"type": "object_ref", "name": "NM1", "ref": ref}]
    assert editor.object_inputs() == expected
    saved = editor.draft()
    editor.clear()
    editor.restore_draft(saved)
    assert editor.object_inputs() == expected
    cursor = editor.textCursor()
    text = editor.toPlainText()
    cursor.setPosition(max(0, len(text) - 4))
    cursor.setPosition(len(text), QTextCursor.KeepAnchor)
    cursor.removeSelectedText()
    assert editor.object_inputs() == []


def test_editing_an_object_token_revokes_its_reference():
    editor = SendOnReturnEdit()
    ref = object_ref(TARGET, INSTANCE)
    editor.insert_object_ref(ref, "NM1")
    cursor = editor.textCursor()
    cursor.setPosition(len(editor.toPlainText()) - 1)
    cursor.deleteChar()
    assert editor.object_inputs() == []


def test_undo_restores_a_revoked_object_reference():
    editor = SendOnReturnEdit()
    ref = object_ref(TARGET, INSTANCE)
    editor.insert_object_ref(ref, "NM1")
    cursor = editor.textCursor()
    cursor.setPosition(len(editor.toPlainText()) - 1)
    cursor.deleteChar()
    assert editor.object_inputs() == []
    editor.undo()
    assert editor.object_inputs() == [{
        "type": "object_ref", "name": "NM1", "ref": ref,
    }]
    editor.redo()
    assert editor.object_inputs() == []


def test_picker_pages_all_objects_in_the_bound_window():
    items = [
        {"kind": "net", "name": f"N{index}", "hierarchy": []}
        for index in range(120)
    ]

    class Provider:
        def __init__(self):
            self.offsets = []
            self.closed = 0

        def selection(self, window_ref, offset=0, max_items=50):
            future = Future()
            future.set_result({
                "ok": True, "valid": True, "schema": "cad.cv.selection.v1",
                "session_ref": "fixture", "window_ref": "wctx_fixture_1",
                "target": TARGET, "items": [], "selection_count": 0,
                "unsupported_count": 0, "max_items": 50, "offset": 0,
                "truncated": False,
            })
            return future

        def objects(self, window_ref, kind="all", query="", offset=0, max_items=50):
            self.offsets.append(offset)
            future = Future()
            future.set_result({
                "ok": True, "valid": True, "schema": "cad.cv.objects.v1",
                "session_ref": "fixture", "window_ref": "wctx_fixture_1",
                "target": TARGET, "items": items[offset:offset + max_items],
                "total": len(items), "max_items": 50, "offset": offset,
                "truncated": offset + max_items < len(items),
                "selection_count": 0,
            })
            return future

        def close(self):
            self.closed += 1

    class Actions:
        def target_picker_provider(self):
            return provider

        def capture(self):
            return "scope"

    class Commands:
        def watch(self, future, *, success=None, failure=None, settled=None, scope=None):
            success(future.result())
            if settled is not None:
                settled(future.result(), None)

    provider = Provider()
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
    window.actions = Actions()
    window.commands = Commands()
    window.lifecycle = SimpleNamespace(closing=False)
    controller = ObjectMentionController(window)
    window.show()
    window.input.setPlainText("@")
    window.input.moveCursor(QTextCursor.End)
    controller.open_at_cursor()
    assert len(controller._object_items) == 50
    controller.popup.loadMoreRequested.emit()
    assert len(controller._object_items) == 100
    controller.popup.loadMoreRequested.emit()
    assert len(controller._object_items) == 120
    assert controller._object_truncated is False
    assert provider.offsets == [0, 50, 100]


def test_picker_loads_current_selection_and_inserts_a_structured_ref():
    selection = {
        "ok": True,
        "valid": True,
        "schema": "cad.cv.selection.v1",
        "session_ref": "fixture",
        "window_ref": "wctx_fixture_1",
        "target": TARGET,
        "items": [INSTANCE],
        "selection_count": 1,
        "unsupported_count": 0,
        "max_items": 50,
        "offset": 0,
        "truncated": False,
    }

    class Provider:
        def __init__(self):
            self.closed = 0
            self.object_calls = 0

        def selection(self, window_ref, offset=0, max_items=50):
            future = Future()
            future.set_result(selection)
            return future

        def objects(self, window_ref, kind="all", query="", offset=0, max_items=50):
            self.object_calls += 1
            future = Future()
            future.set_result({
                "ok": True, "valid": True, "schema": "cad.cv.objects.v1",
                "session_ref": "fixture", "window_ref": "wctx_fixture_1",
                "target": TARGET, "items": [], "total": 0, "max_items": 50,
                "offset": 0, "truncated": False, "selection_count": 1,
            })
            return future

        def close(self):
            self.closed += 1

    class Actions:
        def __init__(self, provider):
            self.provider = provider

        def target_picker_provider(self):
            return self.provider

        def capture(self):
            return "scope"

    class Commands:
        def watch(self, future, *, success=None, failure=None, settled=None, scope=None):
            success(future.result())
            if settled is not None:
                settled(future.result(), None)

    provider = Provider()
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
    window.actions = Actions(provider)
    window.commands = Commands()
    window.lifecycle = SimpleNamespace(closing=False)
    controller = ObjectMentionController(window)
    window.show()
    window.input.setPlainText("@")
    window.input.moveCursor(QTextCursor.End)
    controller.open_at_cursor()
    assert controller.popup.rows.count() == 1
    assert "NM1" in controller.popup.rows.item(0).text()
    assert "器件" in controller.popup.rows.item(0).text()
    assert provider.object_calls == 0
    window.input.setPlainText("@N")
    window.input.moveCursor(QTextCursor.End)
    controller.open_at_cursor()
    assert controller.popup.rows.count() == 1
    assert controller.popup.current_payload()["label"] == "NM1"
    submitted = []
    window.input.submitted.connect(lambda: submitted.append(True))
    QTest.keyClick(window.input, Qt.Key_Return)
    assert submitted == []
    assert window.input.toPlainText() == "@NM1"
    assert window.input.object_inputs()[0]["name"] == "NM1"
    assert provider.closed >= 1
    window.close()


def test_cad_link_click_highlights_without_starting_a_task():
    ref = object_ref(TARGET, INSTANCE)

    class Provider:
        def __init__(self):
            self.closed = 0

        def highlight(self, value, window_ref=None):
            future = Future()
            future.set_result({
                "ok": True, "valid": True, "schema": "cad.cv.highlight.v1",
                "session_ref": "fixture", "target": TARGET, "kind": "instance",
                "name": "NM1", "window_ref": "wctx_fixture_1",
                "highlighted": True, "highlight_count": 1, "zoomed": True,
                "selection_preserved": True,
            })
            return future

        def close(self):
            self.closed += 1

    class Status:
        def __init__(self):
            self.text = ""

        def setText(self, value):
            self.text = value

    provider = Provider()
    window = QWidget()
    window.page = SimpleNamespace(session=SimpleNamespace(session_id="session_1"))
    window.lifecycle = SimpleNamespace(closing=False)
    window.actions = SimpleNamespace(
        target_picker_provider=lambda: provider,
        capture=lambda: "scope",
    )
    window.commands = type("Commands", (), {
        "watch": lambda _self, future, *, success=None, failure=None, settled=None, scope=None:
            (success(future.result()), settled(future.result(), None)),
    })()
    window.status = Status()
    window.info_app = SimpleNamespace(warning=lambda *args: None)
    actions = CADObjectActions(window)
    assert actions.open_link(object_link("session_1", ref)) is True
    assert "NM1" in window.status.text
    assert provider.closed >= 1
    window.close()


def test_cellview_link_click_focuses_without_highlighting_an_object():
    ref = object_ref(TARGET, {
        "kind": "cellview", "name": "worklib/OTA/schematic", "hierarchy": [],
    }, require_master=False)

    class Provider:
        def __init__(self):
            self.closed = 0

        def highlight(self, value, window_ref=None):
            future = Future()
            future.set_result({
                "ok": True, "valid": True, "schema": "cad.cv.highlight.v1",
                "session_ref": "fixture", "target": TARGET, "kind": "cellview",
                "name": value["name"], "window_ref": "wctx_fixture_1",
                "highlighted": False, "highlight_count": 0, "zoomed": True,
                "selection_preserved": True, "bbox": [[0, 0], [1, 1]],
            })
            return future

        def close(self):
            self.closed += 1

    class Status:
        def setText(self, value):
            self.text = value

    provider = Provider()
    window = QWidget()
    window.page = SimpleNamespace(session=SimpleNamespace(session_id="session_1"))
    window.lifecycle = SimpleNamespace(closing=False)
    window.actions = SimpleNamespace(
        target_picker_provider=lambda: provider,
        capture=lambda: "scope",
    )
    window.commands = type("Commands", (), {
        "watch": lambda _self, future, *, success=None, failure=None, settled=None, scope=None:
            (success(future.result()), settled(future.result(), None)),
    })()
    window.status = Status()
    window.info_app = SimpleNamespace(warning=lambda *args: None, notice=lambda *args: None)
    actions = CADObjectActions(window)
    assert actions.open_link(object_link("session_1", ref)) is True
    assert "已置顶" in window.status.text
    window.close()


def test_cad_link_click_reports_selection_and_window_top():
    ref = object_ref(TARGET, INSTANCE)
    cellview = object_ref(TARGET, {
        "kind": "cellview", "name": "worklib/OTA/schematic", "hierarchy": [],
    }, require_master=False)

    class Provider:
        def highlight(self, value, window_ref=None):
            future = Future()
            future.set_result({
                "ok": True, "valid": True, "schema": "cad.cv.highlight.v1",
                "session_ref": "fixture", "target": TARGET, "kind": value["kind"],
                "name": value["name"], "window_ref": "wctx_fixture_1",
                "highlighted": value["kind"] != "cellview",
                "highlight_count": 0 if value["kind"] == "cellview" else 1,
                "zoomed": True, "selection_preserved": value["kind"] == "cellview",
                "selected": value["kind"] != "cellview",
            })
            return future

        def close(self):
            pass

    class Status:
        def setText(self, value):
            self.text = value

    window = QWidget()
    window.page = SimpleNamespace(session=SimpleNamespace(session_id="session_1"))
    window.lifecycle = SimpleNamespace(closing=False)
    window.actions = SimpleNamespace(
        target_picker_provider=lambda: Provider(),
        capture=lambda: "scope",
    )
    window.commands = type("Commands", (), {
        "watch": lambda _self, future, *, success=None, failure=None, settled=None, scope=None:
            (success(future.result()), settled(future.result(), None)),
    })()
    window.status = Status()
    window.info_app = SimpleNamespace(
        warning=lambda *args: None, notice=lambda *args: None)
    actions = CADObjectActions(window)
    actions.open("session_1", ref)
    assert "已选中并高亮" in window.status.text
    actions.open("session_1", cellview)
    assert "已置顶" in window.status.text
    window.close()


def test_input_detail_object_row_opens_the_shared_link():
    ref = object_ref(TARGET, INSTANCE)

    class Cad:
        def open_link(self, value):
            self.value = value
            return True

    cad = Cad()
    window = SimpleNamespace(
        cad_objects=cad,
        page=SimpleNamespace(session=SimpleNamespace(session_id="session_1")),
    )
    assert open_object_input(window, {"type": "object_ref", "ref": ref}) is True
    assert cad.value.startswith("copilot://cad/session_1/")
    assert open_object_input(window, {"type": "mention", "name": "file"}) is False


def test_foreign_session_link_is_rejected_without_a_bridge_call():
    ref = object_ref(TARGET, INSTANCE)
    warnings = []
    called = []
    window = QWidget()
    window.page = SimpleNamespace(session=SimpleNamespace(session_id="current"))
    window.lifecycle = SimpleNamespace(closing=False)
    window.actions = SimpleNamespace(
        target_picker_provider=lambda: called.append(True),
        capture=lambda: "scope",
    )
    window.commands = SimpleNamespace()
    window.info_app = SimpleNamespace(
        warning=lambda title, message: warnings.append((title, message)),
    )
    actions = CADObjectActions(window)
    actions.open("other", ref)
    assert called == []
    assert warnings and "不属于当前会话" in warnings[0][0]
    window.close()


def test_changed_object_signature_is_reported_but_still_located():
    ref = object_ref(TARGET, {**INSTANCE, "bbox": [[1, 2], [3, 4]]})
    notices = []

    class Provider:
        def highlight(self, value, window_ref=None):
            future = Future()
            future.set_result({
                "ok": True, "valid": True, "schema": "cad.cv.highlight.v1",
                "session_ref": "fixture", "target": TARGET, "kind": "instance",
                "name": "NM1", "window_ref": "wctx_fixture_1",
                "highlighted": True, "highlight_count": 1, "zoomed": False,
                "selection_preserved": True, "bbox": [[9, 9], [10, 10]],
            })
            return future

        def close(self):
            pass

    class Status:
        def setText(self, value):
            self.text = value

    window = QWidget()
    window.page = SimpleNamespace(session=SimpleNamespace(session_id="session_1"))
    window.lifecycle = SimpleNamespace(closing=False)
    window.actions = SimpleNamespace(
        target_picker_provider=lambda: Provider(),
        capture=lambda: "scope",
    )
    window.commands = type("Commands", (), {
        "watch": lambda _self, future, *, success=None, failure=None, settled=None, scope=None:
            (success(future.result()), settled(future.result(), None)),
    })()
    window.status = Status()
    window.info_app = SimpleNamespace(
        warning=lambda *args: None,
        notice=lambda title, message: notices.append((title, message)),
    )
    assert CADObjectActions(window).open_link(object_link("session_1", ref)) is True
    assert "已高亮" in window.status.text
    assert notices and notices[0][0] == "对象已变化"
    window.close()
