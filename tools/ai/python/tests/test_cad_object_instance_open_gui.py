"""Explicitly open a missing target on a user-selected Virtuoso instance."""

from __future__ import annotations

from concurrent.futures import Future
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt5.QtWidgets")

from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QWidget
from sico.core.cad_ref import object_ref
from sico_ui import cad_object_instances as instances_module
from sico_ui import cad_objects as objects_module
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
CANDIDATE = "b" * 64


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
            failure(error)
        elif error is None and success is not None:
            success(value)
        if settled is not None:
            settled(value, error)


class DeferredCommands:
    def watch(self, future, *, success=None, failure=None, settled=None, scope=None):
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


class Provider:
    def __init__(self, failure):
        self.failure = failure

    def highlight(self, _ref, _window_ref=None):
        future = Future()
        future.set_exception(self.failure)
        return future

    def close(self):
        pass


def _window(provider, *, commands=None, warnings=None):
    window = QWidget()
    window.page = SimpleNamespace(session=SimpleNamespace(session_id="session_1"))
    window.lifecycle = SimpleNamespace(closing=False)
    window.actions = SimpleNamespace(
        target_picker_provider=lambda: provider,
        capture=lambda: "scope",
    )
    window.commands = commands or SyncCommands()
    window.status = Status()
    window.info_app = SimpleNamespace(
        warning=lambda title, message: warnings.append((title, message)),
        notice=lambda *_args: None,
    )
    return window


def _result():
    return {
        "ok": True,
        "valid": True,
        "schema": "cad.cv.highlight.v1",
        "session_ref": "fixture",
        "target": TARGET,
        "kind": INSTANCE["kind"],
        "name": INSTANCE["name"],
        "window_ref": "wctx_new_1",
        "highlighted": True,
        "highlight_count": 1,
        "zoomed": True,
        "selection_preserved": False,
        "selected": True,
    }


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


def _menu_action(menu, prefix):
    return next(item for item in menu.items if item.text.startswith(prefix))


def _prepare_target_not_open_instance(monkeypatch, *, confirm, warnings, commands=None):
    monkeypatch.setattr(instances_module, "QMenu", Menu)
    monkeypatch.setattr(objects_module, "confirm", confirm)
    provider = Provider(CodedError("target_not_open"))
    window = _window(provider, commands=commands, warnings=warnings)
    ref = object_ref(TARGET, INSTANCE)
    window.actions.object_instances = lambda _ref: _resolved({
        "instances": [{
            "candidate": CANDIDATE,
            "instance": "instance-2",
            "generation": "generation-2",
            "windows": [],
        }],
    })
    actions = CADObjectActions(window)
    actions.open("session_1", ref)
    _menu_action(actions._instance_menu, "查找项目内其他").trigger()
    action = _menu_action(actions._instance_menu, "其他实例")
    assert "目标未打开" in action.text
    return window, ref, actions, action


def test_selected_instance_target_opens_read_only_then_highlights(monkeypatch):
    warnings = []
    opened = []
    located = []
    window, ref, _actions, action = _prepare_target_not_open_instance(
        monkeypatch,
        confirm=lambda *_args, **_kwargs: True,
        warnings=warnings,
    )

    def open_target(value, *, candidate):
        opened.append((value, candidate))
        return _resolved({
            "target": TARGET,
            "window_ref": "wctx_new_1",
            "window": "window:9",
        })

    def locate(value, candidate, window_ref):
        located.append((value, candidate, window_ref))
        return _resolved({"result": _result()})

    window.actions.open_object_target = open_target
    window.actions.highlight_object_instance = locate
    action.trigger()
    assert opened == [(ref, CANDIDATE)]
    assert located == [(ref, CANDIDATE, "wctx_new_1")]
    assert "已选中并高亮" in window.status.text
    assert warnings == []
    window.close()


def test_cancel_does_not_open_selected_instance_target(monkeypatch):
    warnings = []
    opened = []
    window, _ref, _actions, action = _prepare_target_not_open_instance(
        monkeypatch,
        confirm=lambda *_args, **_kwargs: False,
        warnings=warnings,
    )
    window.actions.open_object_target = lambda *args: opened.append(args)
    action.trigger()
    assert opened == []
    assert window.status.text == "已取消打开目标实例"
    assert warnings == []
    window.close()


def test_selected_instance_open_failure_is_reported(monkeypatch):
    warnings = []
    window, _ref, _actions, action = _prepare_target_not_open_instance(
        monkeypatch,
        confirm=lambda *_args, **_kwargs: True,
        warnings=warnings,
    )

    def open_target(_value, *, candidate):
        future = Future()
        future.set_exception(RuntimeError("目标实例已不可用"))
        return future

    window.actions.open_object_target = open_target
    action.trigger()
    assert warnings[0][0] == "无法在目标实例打开并定位"
    assert "不可用" in warnings[0][1]
    window.close()


def test_selected_instance_open_timeout_is_reported(monkeypatch):
    warnings = []
    window, _ref, actions, action = _prepare_target_not_open_instance(
        monkeypatch,
        confirm=lambda *_args, **_kwargs: True,
        warnings=warnings,
        commands=DeferredCommands(),
    )
    window.actions.open_object_target = lambda *_args, **_kwargs: Future()
    actions.OPEN_TIMEOUT_MS = 20
    action.trigger()
    QTest.qWait(60)
    assert warnings[0][0] == "目标实例打开超时"
    assert "不会自动重试" in warnings[0][1]
    window.close()
