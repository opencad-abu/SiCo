"""Standalone home entry delegates only inside the private GUI input context."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from sico_ui import home_desktop


def test_run_home_wraps_the_home_process_in_gui_input(monkeypatch, tmp_path):
    events = []

    @contextmanager
    def gui_input(directory):
        events.append(("enter", directory))
        yield
        events.append(("exit", directory))

    monkeypatch.setattr("sico.service.gui_input.gui_input", gui_input)
    monkeypatch.setattr(home_desktop, "_run_home",
                        lambda args: events.append(("run", args.launch_dir)) or 7)
    args = SimpleNamespace(launch_dir=tmp_path)
    assert home_desktop.run_home(args) == 7
    assert events == [
        ("enter", tmp_path),
        ("run", tmp_path),
        ("exit", tmp_path),
    ]


def test_run_home_builds_home_and_cleans_owned_startup(monkeypatch, tmp_path):
    events = []

    class Signal:
        def __init__(self):
            self.slots = []

        def connect(self, slot):
            self.slots.append(slot)

        def fire(self):
            for slot in self.slots:
                slot()

        def disconnect(self, slot):
            self.slots.remove(slot)

    class Timer:
        def __init__(self):
            self.timeout = Signal()

        def setInterval(self, _value):
            pass

        def start(self, _value):
            pass

        def stop(self):
            events.append("timer.stop")

        @staticmethod
        def singleShot(_delay, callback):
            callback()

    class App:
        aboutToQuit = Signal()

        @classmethod
        def instance(cls):
            return None

        def __init__(self, *_args):
            pass

        def setQuitOnLastWindowClosed(self, _value):
            pass

        def exec_(self):
            return 4

        def quit(self):
            pass

    class Startup:
        def __init__(self, *_args):
            self.ready = object()
            self.closed = 0
            self.waited = 0

        def close(self):
            self.closed += 1

        def wait(self, _timeout):
            self.waited += 1
            return True

    class Home:
        def __init__(self, *_args, **_kwargs):
            self.lifecycle = SimpleNamespace(closing=False)
            self.closed = Signal()
            self.api = object()
            self.timer = SimpleNamespace(stop=lambda: events.append("home.timer.stop"))
            self.platform_menu = SimpleNamespace(close=lambda: events.append("menu.close"))
            self.ready = object()
            self.failed = object()
            self.geometry = lambda: "geometry"
            self.retry = None
            self.show = lambda: events.append("home.show")
            self.hide = lambda: events.append("home.hide")
            self.close = lambda: events.append("home.close")

    class Receipt:
        def __init__(self, _home):
            self.watched = []

        def watch(self, *args):
            self.watched.append(args)

        def clear(self):
            events.append("receipt.clear")

    monkeypatch.setattr("PyQt5.QtCore.QTimer", Timer)
    monkeypatch.setattr("PyQt5.QtWidgets.QApplication", App)
    monkeypatch.setattr("sico_ui.branding.install_logo", lambda _app: None)
    monkeypatch.setattr("sico_ui.appearance_runtime.apply_saved_appearance",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr("sico_ui.qt_translation.install_chinese_qt_translations",
                        lambda _app: None)
    monkeypatch.setattr("sico_ui.home_window.HomeWindow", Home)
    monkeypatch.setattr("sico_ui.receipts.DataReceipt", Receipt)
    monkeypatch.setattr("sico_ui.service_connection.attach_service_connection",
                        lambda *_args: None)
    monkeypatch.setattr("sico_ui.wheel.prepare_wheel_environment", lambda: None)
    monkeypatch.setattr("sico_ui.window.AssistantWindow", lambda *_args: None)
    monkeypatch.setattr("sico.service.desktop.DesktopShutdown",
                        lambda: SimpleNamespace(arm=lambda *_args: None,
                                                remaining=lambda: 0))
    monkeypatch.setattr("sico.service.home_startup.HomeStartup", Startup)
    monkeypatch.setattr("sico.service.termination.TerminationSignal",
                        lambda: SimpleNamespace(
                            install=lambda: SimpleNamespace(
                                received=SimpleNamespace(is_set=lambda: False),
                                restore=lambda: events.append("termination.restore")),
                        ))
    monkeypatch.setattr("sico.service.launch.prepare_project_state", lambda _path: None)
    result = home_desktop._run_home(SimpleNamespace(launch_dir=tmp_path))
    assert result == 4
    assert "home.show" in events and "home.close" in events
    assert "termination.restore" in events and "receipt.clear" in events
