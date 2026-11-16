"""Deleted CAD targets fail visibly instead of hanging or crashing the desktop."""

from __future__ import annotations

from concurrent.futures import Future
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt5.QtWidgets")

from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QWidget
from sico.core.cad_ref import object_ref
from sico_ui import cad_object_instances as instances_module
from sico_ui import cad_objects as cad_objects_module
from sico_ui.cad_objects import CADObjectActions

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


class CodedError(RuntimeError):
    def __init__(self, code, message="target unavailable"):
        super().__init__(message)
        self.code = code


class SyncCommands:
    def watch(self, future, *, success=None, failure=None, settled=None, scope=None):
        try:
            value = future.result()
        except Exception as exc:
            value, error = None, exc
        else:
            error = None
        if error is not None and failure is not None:
            try:
                failure(error)
            except Exception:
                pass
        elif error is None and success is not None:
            success(value)
        if settled is not None:
            settled(value, error)


class DeferredCommands:
    def __init__(self):
        self.calls = []

    def watch(self, future, *, success=None, failure=None, settled=None, scope=None):
        self.calls.append((future, success, failure, settled))

        def done(receipt):
            try:
                value = receipt.result()
            except Exception as exc:
                if failure is not None:
                    failure(exc)
            else:
                if success is not None:
                    success(value)
            if settled is not None:
                settled(*_outcome(receipt))

        future.add_done_callback(done)


def _outcome(future):
    try:
        return future.result(), None
    except Exception as exc:
        return None, exc


def _resolved(value):
    future = Future()
    future.set_result(value)
    return future


class Status:
    def __init__(self):
        self.text = ""

    def setText(self, value):
        self.text = value


def make_window(*providers, commands=None, warnings=None, notices=None):
    rows = list(providers)

    def provider():
        return rows.pop(0) if len(rows) > 1 else rows[0]

    window = QWidget()
    window.page = SimpleNamespace(session=SimpleNamespace(session_id="session_1"))
    window.lifecycle = SimpleNamespace(closing=False)
    window.actions = SimpleNamespace(
        target_picker_provider=provider,
        capture=lambda: "scope",
    )
    window.commands = commands or SyncCommands()
    window.status = Status()
    window.info_app = SimpleNamespace(
        warning=lambda title, message: (warnings if warnings is not None else []).append(
            (title, message)
        ),
        notice=lambda title, message: (notices if notices is not None else []).append(
            (title, message)
        ),
    )
    return window


def result(value=INSTANCE, *, selected=False):
    return {
        "ok": True,
        "valid": True,
        "schema": "cad.cv.highlight.v1",
        "session_ref": "fixture",
        "target": TARGET,
        "kind": value["kind"],
        "name": value["name"],
        "window_ref": "wctx_fixture_1",
        "highlighted": True,
        "highlight_count": 1,
        "zoomed": True,
        "selection_preserved": True,
        "selected": selected,
    }


class Provider:
    def __init__(self, *, failure=None, pending=False, value=INSTANCE):
        self.failure = failure
        self.pending = pending
        self.value = value
        self.closed = 0
        self.futures = []
        self.window_refs = []

    def highlight(self, _ref, window_ref=None):
        self.window_refs.append(window_ref)
        future = Future()
        self.futures.append(future)
        if self.pending:
            return future
        if self.failure is not None:
            future.set_exception(self.failure)
        else:
            future.set_result(result(self.value, selected=True))
        return future

    def close(self):
        self.closed += 1


@pytest.mark.parametrize("code,title", [
    ("stale_object", "对象已不存在"),
    ("stale_context", "原窗口已变化"),
    ("object_changed", "对象已变化"),
    ("highlight_failed", "对象无法高亮"),
    ("read_failed", "对象读取失败"),
    ("unsupported_object", "对象类型不支持"),
])
def test_deleted_or_changed_targets_have_specific_warnings(code, title):
    warnings = []
    provider = Provider(failure=CodedError(code))
    window = make_window(provider, warnings=warnings)
    CADObjectActions(window).open("session_1", object_ref(TARGET, INSTANCE))
    assert warnings[0][0] == title
    assert provider.closed >= 1
    window.close()


def test_target_not_open_opens_read_only_then_highlights(monkeypatch):
    warnings = []
    first = Provider(failure=CodedError("target_not_open"))
    second = Provider()
    opened = []
    window = make_window(first, second, warnings=warnings)
    ref = object_ref(TARGET, INSTANCE)

    def open_target(value):
        opened.append(value)
        future = Future()
        future.set_result({
            "target": TARGET, "window_ref": "wctx_new_1", "window": "window:9",
        })
        return future

    window.actions.open_object_target = open_target
    monkeypatch.setattr(
        cad_objects_module, "confirm",
        lambda _parent, _title, _message, *, choice: True,
    )
    CADObjectActions(window).open("session_1", ref)
    assert opened == [ref]
    assert second.window_refs == ["wctx_new_1"]
    assert "已选中并高亮" in window.status.text
    assert warnings == []
    window.close()


def test_target_not_open_can_be_cancelled_without_opening(monkeypatch):
    warnings = []
    provider = Provider(failure=CodedError("target_not_open"))
    opened = []
    window = make_window(provider, warnings=warnings)
    window.actions.open_object_target = lambda value: opened.append(value)
    monkeypatch.setattr(
        cad_objects_module, "confirm",
        lambda _parent, _title, _message, *, choice: False,
    )
    CADObjectActions(window).open("session_1", object_ref(TARGET, INSTANCE))
    assert opened == []
    assert window.status.text == "已取消打开目标窗口"
    assert warnings == []
    window.close()


def test_target_open_failure_is_reported(monkeypatch):
    warnings = []
    provider = Provider(failure=CodedError("target_not_open"))
    window = make_window(provider, warnings=warnings)

    def open_target(_ref):
        future = Future()
        future.set_exception(RuntimeError("当前窗口没有控制权"))
        return future

    window.actions.open_object_target = open_target
    monkeypatch.setattr(
        cad_objects_module, "confirm",
        lambda _parent, _title, _message, *, choice: True,
    )
    CADObjectActions(window).open("session_1", object_ref(TARGET, INSTANCE))
    assert warnings[0][0] == "无法打开并定位"
    assert "控制权" in warnings[0][1]
    window.close()


def test_target_open_timeout_does_not_hang(monkeypatch):
    warnings = []
    provider = Provider(failure=CodedError("target_not_open"))
    window = make_window(provider, commands=DeferredCommands(), warnings=warnings)
    window.actions.open_object_target = lambda _ref: Future()
    monkeypatch.setattr(
        cad_objects_module, "confirm",
        lambda _parent, _title, _message, *, choice: True,
    )
    actions = CADObjectActions(window)
    monkeypatch.setattr(actions, "OPEN_TIMEOUT_MS", 20)
    actions.open("session_1", object_ref(TARGET, INSTANCE))
    QTest.qWait(60)
    assert warnings[0][0] == "打开目标超时"
    assert "不会自动重试" in warnings[0][1]
    window.close()


def test_target_not_open_without_offer_keeps_the_regular_warning():
    warnings = []
    provider = Provider(failure=CodedError("target_not_open"))
    window = make_window(provider, warnings=warnings)
    CADObjectActions(window)._highlight(
        object_ref(TARGET, INSTANCE), provider=provider, allow_open=False,
    )
    assert warnings[0][0] == "目标窗口未打开"
    window.close()


def test_target_not_open_can_locate_on_an_explicit_other_instance(monkeypatch):
    class Signal:
        def __init__(self):
            self.callback = None

        def connect(self, callback):
            self.callback = callback

    class Action:
        def __init__(self, text):
            self.text, self.triggered = text, Signal()

        def trigger(self):
            self.triggered.callback(False)

    class Menu:
        def __init__(self, _parent):
            self.items = []

        def addAction(self, text):
            action = Action(text)
            self.items.append(action)
            return action

        def addSeparator(self):
            pass

        def popup(self, *_args):
            pass

    monkeypatch.setattr(instances_module, "QMenu", Menu)
    warnings = []
    provider = Provider(failure=CodedError("target_not_open"))
    window = make_window(provider, warnings=warnings)
    ref = object_ref(TARGET, INSTANCE)
    candidate = "a" * 64
    located = []
    window.actions.object_instances = lambda _ref: _resolved({
        "instances": [{
            "candidate": candidate, "instance": "instance-2",
            "generation": "generation-2",
            "windows": [{
                "window_ref": "wctx_other_1", "window": "window:9",
                "title": "OTA schematic",
            }],
        }],
    })

    def locate(value, selected, window_ref):
        located.append((value, selected, window_ref))
        return _resolved({"result": result(selected=True)})

    window.actions.highlight_object_instance = locate
    actions = CADObjectActions(window)
    actions.open("session_1", ref)
    first = actions._instance_menu
    find = next(item for item in first.items
                if item.text.startswith("查找项目内其他"))
    find.trigger()
    second = actions._instance_menu
    other = next(item for item in second.items
                 if item.text.startswith("其他实例"))
    other.trigger()
    assert located == [(ref, candidate, "wctx_other_1")]
    assert "已选中并高亮" in window.status.text
    assert warnings == []
    window.close()


def test_ambiguous_target_with_a_chosen_window_has_a_specific_warning():
    warnings = []
    provider = Provider(failure=CodedError("ambiguous_target"))
    window = make_window(provider, warnings=warnings)
    CADObjectActions(window)._highlight(
        object_ref(TARGET, INSTANCE), provider=provider,
        window_ref="wctx_fixture_1",
    )
    assert warnings[0][0] == "目标窗口不唯一"
    window.close()


def test_unknown_target_failure_keeps_the_raw_detail():
    warnings = []
    provider = Provider(failure=CodedError("bridge_lost", "relay process exited"))
    window = make_window(provider, warnings=warnings)
    CADObjectActions(window).open("session_1", object_ref(TARGET, INSTANCE))
    assert warnings[0][0] == "无法定位对象"
    assert "relay process exited" in warnings[0][1]
    window.close()


def test_unexpected_provider_error_is_contained_and_reported():
    warnings = []

    class BrokenProvider(Provider):
        def highlight(self, _ref, _window_ref=None):
            raise KeyError("poisoned provider state")

    window = make_window(BrokenProvider(), warnings=warnings)
    CADObjectActions(window).open("session_1", object_ref(TARGET, INSTANCE))
    assert warnings[0][0] == "无法定位对象"
    assert "poisoned provider state" in warnings[0][1]
    window.close()


def test_unanswered_object_location_times_out_and_closes_the_provider(monkeypatch):
    warnings = []
    provider = Provider(pending=True)
    window = make_window(provider, commands=DeferredCommands(), warnings=warnings)
    actions = CADObjectActions(window)
    monkeypatch.setattr(actions, "TIMEOUT_MS", 20)
    actions.open("session_1", object_ref(TARGET, INSTANCE))
    assert warnings == []
    QTest.qWait(60)
    assert warnings[0][0] == "对象定位超时"
    assert "不会自动重试" in warnings[0][1]
    assert provider.closed == 1
    window.close()


def test_unanswered_window_list_times_out(monkeypatch):
    warnings = []
    first = Provider(failure=CodedError("ambiguous_target"))
    second = Provider(pending=True)
    second.windows_called = 0

    def windows(_ref):
        second.windows_called += 1
        future = Future()
        second.futures.append(future)
        return future

    second.windows = windows
    window = make_window(first, second, commands=DeferredCommands(), warnings=warnings)
    actions = CADObjectActions(window)
    monkeypatch.setattr(actions, "WINDOW_TIMEOUT_MS", 20)
    actions.open("session_1", object_ref(TARGET, INSTANCE))
    assert second.windows_called == 1
    QTest.qWait(60)
    assert warnings[0][0] == "窗口列表读取超时"
    assert second.closed == 1
    window.close()


def test_a_late_failure_from_an_old_click_is_ignored():
    warnings = []
    first = Provider(pending=True)
    second = Provider(pending=True)
    commands = DeferredCommands()
    window = make_window(first, second, commands=commands, warnings=warnings)
    actions = CADObjectActions(window)
    ref = object_ref(TARGET, INSTANCE)
    actions.open("session_1", ref)
    actions.open("session_1", ref)
    first.futures[0].set_exception(CodedError("stale_object"))
    assert warnings == []
    second.futures[0].set_exception(CodedError("stale_object"))
    assert warnings[0][0] == "对象已不存在"
    window.close()


def test_mismatched_bridge_result_is_reported_without_claiming_a_selection():
    warnings = []
    other = {**TARGET, "cell": "OTHER"}

    class WrongProvider(Provider):
        def highlight(self, _ref, _window_ref=None):
            future = Future()
            value = result()
            value["target"] = other
            future.set_result(value)
            return future

    provider = WrongProvider()
    window = make_window(provider, warnings=warnings)
    CADObjectActions(window).open("session_1", object_ref(TARGET, INSTANCE))
    assert warnings[0][0] == "对象已变化"
    assert "未确认定位" in warnings[0][1]
    assert "已选中" not in window.status.text
    window.close()
