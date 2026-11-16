"""Multi-window object refs keep target and transient window provenance."""

from __future__ import annotations

from concurrent.futures import Future
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt5.QtWidgets")

from PyQt5.QtGui import QTextCursor
from PyQt5.QtWidgets import QWidget
from sico_ui.input import SendOnReturnEdit
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


def test_picker_switches_other_window_and_keeps_window_qualified_ref():
    other_target = {
        "library": "worklib", "cell": "OTA", "view_name": "layout",
        "oa_view_type": "maskLayout", "oa_view_type_source": "platform_api",
    }
    selection = {
        "ok": True, "valid": True, "schema": "cad.cv.selection.v1",
        "session_ref": "fixture", "window_ref": "wctx_fixture_1",
        "target": TARGET, "items": [INSTANCE], "selection_count": 1,
        "unsupported_count": 0, "max_items": 50, "offset": 0, "truncated": False,
    }
    other_selection = {
        **selection, "window_ref": "wctx_fixture_2", "target": other_target,
    }
    windows = {
        "session_ref": "fixture",
        "windows": [
            {"window_ref": "wctx_fixture_1", "window": "window:1",
             "title": "Schematic", "target": TARGET, "kind": "schematic",
             "is_current": True},
            {"window_ref": "wctx_fixture_2", "window": "window:5",
             "title": "Layout", "target": other_target, "kind": "layout",
             "is_current": False},
        ],
        "total": 2, "truncated": False,
    }

    class Provider:
        def selection(self, window_ref, offset=0, max_items=50):
            future = Future()
            future.set_result(other_selection if window_ref == "wctx_fixture_2" else selection)
            return future

        def objects(self, window_ref, kind="all", query="", offset=0, max_items=50):
            target = other_target if window_ref == "wctx_fixture_2" else TARGET
            future = Future()
            future.set_result({
                "ok": True, "valid": True, "schema": "cad.cv.objects.v1",
                "session_ref": "fixture", "window_ref": window_ref,
                "target": target, "items": [INSTANCE], "total": 1,
                "max_items": 50, "offset": 0, "truncated": False,
                "selection_count": 1,
            })
            return future

        def window_list(self):
            future = Future()
            future.set_result(windows)
            return future

        def close(self):
            pass

    class Commands:
        def watch(self, future, *, success=None, failure=None, settled=None, scope=None):
            success(future.result())

    window = QWidget()
    window.input = SendOnReturnEdit(window)
    window.presentation = SimpleNamespace(
        submission_context=SimpleNamespace(snapshot={
            "window_ref": "wctx_fixture_1",
            "window": "window:1",
            "cellview": {
                "lib": "worklib", "cell": "OTA", "view_name": "schematic",
                "oa_view_type": "schematic",
                "oa_view_type_source": "platform_api",
            },
        })
    )
    window.actions = SimpleNamespace(
        target_picker_provider=lambda: Provider(),
        capture=lambda: None,
    )
    window.commands = Commands()
    window.lifecycle = SimpleNamespace(closing=False)
    controller = ObjectMentionController(window)
    window.show()
    try:
        window.input.setPlainText("@")
        window.input.moveCursor(QTextCursor.End)
        controller.open_at_cursor()
        labels = [
            controller.popup.rows.item(index).text()
            for index in range(controller.popup.rows.count())
        ]
        assert any("window:5" in label and "layout" in label for label in labels)
        other_row = next(
            index for index, label in enumerate(labels)
            if "window:5" in label and "layout" in label
        )
        controller.popup.rows.setCurrentRow(other_row)
        controller.choose()
        assert controller._window_ref == "wctx_fixture_2"
        labels = [
            controller.popup.rows.item(index).text()
            for index in range(controller.popup.rows.count())
        ]
        assert any("window:5" in label and "NM1" in label for label in labels)
        row = next(
            index for index, label in enumerate(labels)
            if "window:5" in label and "NM1" in label
        )
        controller.popup.rows.setCurrentRow(row)
        controller.choose()
        assert window.input.object_inputs()[0]["window_ref"] == "wctx_fixture_2"
        assert "window:5" in window.input.object_inputs()[0]["window_label"]
    finally:
        window.close()
