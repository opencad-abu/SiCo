"""IME composition and ASCII IPC preserve exact Chinese/multiline input."""

from __future__ import annotations

import json
import os
import sys
from concurrent.futures import Future
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QInputMethodEvent, QTextCursor
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
from sico.core.contracts import BoundContext
from sico.service.launch import desktop_environment
from sico.transport.quick_codec import decode_submission, encode_text
from sico_ui.composer import QuickComposer


@pytest.fixture(autouse=True)
def current_installation(monkeypatch):
    monkeypatch.delenv("CAD_HOME", raising=False)
    monkeypatch.setenv("SICO_HOME", str(Path(__file__).resolve().parents[4]))


def test_return_sends_and_shift_return_keeps_multiline_draft():
    _app = QApplication.instance() or QApplication([])
    sent = []
    window = QuickComposer({"source": "测试库 / 电路", "destination": "Session: test"}, sent.append)
    window.restore()
    try:
        window.input.setPlainText("第一行")
        window.input.moveCursor(QTextCursor.End)
        QTest.keyClick(window.input, Qt.Key_Return, Qt.ShiftModifier)
        assert window.input.toPlainText() == "第一行\n"
        assert sent == []

        QTest.keyClick(window.input, Qt.Key_Return)
        assert len(sent) == 1
        assert sent == ["第一行\n"]
        assert window.input.isReadOnly()
    finally:
        window.finished = True
        window.close()


def test_ime_preedit_commit_rejection_retry_and_acceptance():
    app = QApplication.instance() or QApplication([])
    sent = []
    window = QuickComposer({"source": "测试库 / 电路", "destination": "Session: test"}, sent.append)
    window.restore()
    try:
        assert window.input.testAttribute(Qt.WA_InputMethodEnabled)
        app.sendEvent(window.input, QInputMethodEvent("zhongwen", []))
        assert window.input.toPlainText() == ""
        committed = QInputMethodEvent()
        text = '中文需求：读取当前窗口。\n引号"，反斜线\\，符号 Ω 🚀'
        committed.setCommitString(text)
        app.sendEvent(window.input, committed)
        assert window.input.toPlainText() == text
        window.submit()
        window.submit()
        assert sent == [text]
        window.reject()
        assert window.isVisible() and window.input.isReadOnly()
        window.receive({"kind": "rejected", "message": "会话已结束；文字保留。"})
        assert window.input.toPlainText() == text
        assert not window.input.isReadOnly() and window.submit_button.isEnabled()
        window.submit()
        window.receive({"kind": "accepted"})
        assert not window.isVisible() and window.finished
    finally:
        window.finished = True
        window.close()


def test_unknown_receipt_keeps_draft_distinct_from_rejection():
    _app = QApplication.instance() or QApplication([])
    sent = []
    window = QuickComposer({"source": "测试库 / 电路", "destination": "Session: test"}, sent.append)
    window.restore()
    try:
        window.input.setPlainText("保留这段待核对的需求")
        window.submit()
        window.receive({"kind": "unknown", "message": "结果未知，请查询原请求。"})
        assert window.input.toPlainText() == "保留这段待核对的需求"
        assert not window.pending and not window.input.isReadOnly()
        assert not window.submit_button.isEnabled() and window.cancel.isEnabled()
        assert window.submit_button.text() == "查询原请求"
        assert window.status.text() == "结果未知，请查询原请求。"
        assert not window.finished
    finally:
        window.finished = True
        window.close()


@pytest.mark.parametrize("restore", [False, True])
def test_unknown_queries_original_once_preserves_draft_on_query_failure(restore):
    _app = QApplication.instance() or QApplication([])
    sent, queried = [], []
    future = Future()

    def query():
        queried.append("original")
        return future

    window = QuickComposer(dict(source="测试库 / 电路", unconfirmed=restore,
                                draft_text="原始请求"), sent.append, query)
    try:
        if not restore:
            window.input.setPlainText("原始请求")
            window.submit()
            window.receive(dict(kind="unknown", message="原请求结果未知"))
        assert window.submit_button.text() == "查询原请求"
        window.submit()
        window.submit()
        assert queried == ["original"] and window.pending
        future.set_exception(OSError("query pipe failed"))
        window._send_receipt.poll()
        assert window.unconfirmed and not window.pending and window.submit_button.isEnabled()
        assert window.input.toPlainText() == "原始请求"
        assert sent == ([] if restore else ["原始请求"])
        window.submit()
        assert queried == ["original", "original"]
        window.receive(dict(kind="accepted"))
        assert window.finished
    finally:
        window.finished = True
        window.close()


def test_quick_composer_send_button_uses_the_accent_action_surface():
    _app = QApplication.instance() or QApplication([])
    window = QuickComposer(
        {"source": "测试库 / 电路", "destination": "Session: test"}, lambda _text: None
    )
    try:
        assert window.submit_button.property("bottomAction") is True
        assert window.cancel.property("bottomAction") is not True
        rule = window.styleSheet().split(
            'QPushButton[bottomAction="true"],', 1
        )[1].split("}", 1)[0]
        assert "background-color:" in rule
    finally:
        window.finished = True
        window.close()


def test_quick_composer_action_buttons_keep_space_above_the_bottom_edge():
    app = QApplication.instance() or QApplication([])
    window = QuickComposer(
        {"source": "测试库 / 电路", "destination": "Session: test"}, lambda _text: None
    )
    window.show()
    app.processEvents()
    try:
        for button in (window.cancel, window.submit_button):
            button_bottom = button.geometry().y() + button.geometry().height()
            assert window.body.height() - button_bottom == 8
    finally:
        window.finished = True
        window.close()


def test_quick_composer_uses_skill_screen_point_with_legacy_fallback():
    _app = QApplication.instance() or QApplication([])
    positioned = []

    class Probe(QuickComposer):
        def center_on_point(self, point):
            positioned.append(("point", point.x(), point.y()))

        def center_on_anchor(self, anchor=None):
            positioned.append(("anchor", None))

    window = Probe(
        {
            "source": "测试库 / 电路",
            "destination": "Session: test",
            "screen_point": {"kind": "pointer", "x": -120, "y": 480},
        },
        lambda _text: None,
    )
    try:
        assert positioned == [("point", -120, 480)]
    finally:
        window.finished = True
        window.close()

    positioned.clear()
    window = Probe(
        {
            "source": "测试库 / 电路",
            "destination": "Session: test",
            "screen_point": {"x": "invalid", "y": 480},
        },
        lambda _text: None,
    )
    try:
        assert positioned == [("anchor", None)]
    finally:
        window.finished = True
        window.close()


def test_quick_composer_object_picker_uses_captured_ask_window():
    _app = QApplication.instance() or QApplication([])
    sent, requests = [], []
    target = {
        "library": "worklib", "cell": "OTA", "view_name": "schematic",
        "oa_view_type": "schematic", "oa_view_type_source": "platform_api",
    }
    context = {
        "window_ref": "wctx_fixture_1",
        "cellview": {
            "lib": "worklib", "cell": "OTA", "view_name": "schematic",
            "oa_view_type": "schematic",
            "oa_view_type_source": "platform_api",
        },
    }
    window = QuickComposer(
        {"source": "测试库 / 电路", "destination": "Session: test", "context": context},
        sent.append,
        object_request=requests.append,
    )
    window.show()
    try:
        controller = window.object_mentions
        window.input.setPlainText("检查 @")
        window.input.moveCursor(QTextCursor.End)
        controller.open_at_cursor()

        def respond(request):
            fields = request.split(" ")
            request_id = fields[1]
            if fields[0] == "selection":
                value = {
                    "ok": True, "valid": True, "schema": "cad.cv.selection.v1",
                    "session_ref": "fixture", "window_ref": "wctx_fixture_1",
                    "target": target, "items": [], "selection_count": 0,
                    "unsupported_count": 0, "max_items": 50, "offset": 0,
                    "truncated": False,
                }
            elif fields[0] == "objects":
                value = {
                    "ok": True, "valid": True, "schema": "cad.cv.objects.v1",
                    "session_ref": "fixture", "window_ref": "wctx_fixture_1",
                    "target": target, "items": [{
                        "kind": "net", "name": "VOUT", "hierarchy": [],
                    }],
                    "total": 1, "max_items": 50, "offset": 0,
                    "truncated": False, "selection_count": 0,
                }
            else:
                value = {
                    "ok": True, "valid": True, "schema": "cad.windows.v1",
                    "session_ref": "fixture", "pid": 123, "windows": [],
                    "max_items": 200, "total": 0, "offset": 0,
                    "next_offset": None, "truncated": False,
                    "snapshot_atomic": False, "read_scope": "current Virtuoso process only",
                }
            window.receive({
                "kind": "objects",
                "message": json.dumps({"id": request_id, "value": value}),
            })

        assert [request.split(" ")[0] for request in requests] == ["selection"]
        respond(requests[0])
        assert [request.split(" ")[0] for request in requests] == [
            "selection", "objects", "windows",
        ]
        for request in requests[1:]:
            respond(request)
        assert controller.popup.rows.count() >= 2
        assert any(
            "VOUT" in controller.popup.rows.item(index).text()
            for index in range(controller.popup.rows.count())
        )
        row = next(
            index for index in range(controller.popup.rows.count())
            if "VOUT" in controller.popup.rows.item(index).text()
        )
        controller.popup.rows.setCurrentRow(row)
        controller.choose()
        window.submit()
        assert sent and "User-selected CAD objects" in sent[0]
        assert '"name": "VOUT"' in sent[0]
    finally:
        window.finished = True
        window.close()


def test_encoded_input_roundtrip_and_strict_validation():
    text = '读取上下文\n"literal" \\ $()'
    wire = {
        "kind": "submit",
        "id": "q1",
        "text_b64": encode_text(text),
        "context": BoundContext("i", "g", "q1", {"valid": True}).record(),
    }
    assert decode_submission(json.dumps(wire).encode())["text"] == text
    for invalid in ("@invalid", "/w==", "AAAA", ""):
        with pytest.raises(ValueError):
            decode_submission(json.dumps({**wire, "text_b64": invalid}).encode())
    with pytest.raises(ValueError):
        encode_text("中" * 16001)
    assert len(encode_text("🚀" * 16000)) < 86000


def test_composer_environment_keeps_ime_and_private_nas_paths(tmp_path):
    runtime_python = Path(__file__).resolve().parents[4] / "runtime/python/bin/python3"
    variables = {
        "QT_IM_MODULE": "ibus",
        "XMODIFIERS": "@im=ibus",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/example/bus",
        "IBUS_ADDRESS": "unix:path=/example/ibus",
        "LANG": "zh_CN.UTF-8",
        "SICO_PYTHON": str(runtime_python),
    }
    env = desktop_environment(
        tmp_path, "quick-test", {**variables, "QT_PLUGIN_PATH": "/eda/qt", "QT_XCB_NO_XI2": "1"}
    )
    assert all(env[k] == value for k, value in variables.items())
    assert "QT_PLUGIN_PATH" not in env and "QT_XCB_NO_XI2" not in env
    assert env["TMPDIR"] == str(tmp_path / ".sico/ai/agent/runtime/quick-test")
