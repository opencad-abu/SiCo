"""Qt local startup owns the event loop, shutdown guard and terminal cleanup."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from sico.service import local_startup


class Signal:
    def __init__(self):
        self.callbacks = []
        self.disconnected = []
        self.state = False

    def connect(self, callback):
        self.callbacks.append(callback)

    def disconnect(self, callback):
        self.disconnected.append(callback)
        self.callbacks.remove(callback)

    def emit(self):
        for callback in tuple(self.callbacks):
            callback()

    def set(self):
        self.state = True

    def is_set(self):
        return self.state


class Timer:
    instances = []
    single_shots = []

    @classmethod
    def singleShot(cls, delay, callback):
        cls.single_shots.append((delay, callback))

    def __init__(self):
        self.timeout = Signal()
        self.interval = None
        self.started = False
        self.stopped = 0
        Timer.instances.append(self)

    def setInterval(self, value):
        self.interval = value

    def start(self, _value=None):
        self.started = True

    def stop(self):
        self.stopped += 1


class Application:
    current = None

    @classmethod
    def instance(cls):
        return cls.current

    def __init__(self, _args):
        self.aboutToQuit = Signal()
        self.quit_calls = 0
        self.quit_on_close = None
        self.last_window = None
        self.result = 0
        Application.current = self

    def setQuitOnLastWindowClosed(self, value):
        self.quit_on_close = value

    def exec_(self):
        return self.result

    def quit(self):
        self.quit_calls += 1


class Shutdown:
    PARENT_EXIT_GRACE_SECONDS = 12

    def __init__(self):
        self.armed = []

    def arm(self, value=None):
        self.armed.append(value)

    def remaining(self):
        return 4


class Termination:
    def __init__(self):
        self.received = Signal()
        self.restored = 0

    def install(self):
        return self

    def restore(self):
        self.restored += 1


class Startup:
    def __init__(self, args, notice):
        self.args, self.notice = args, notice
        self.closed = 0
        self.waited = []

    def wait(self, timeout):
        self.waited.append(timeout)
        return True


class View:
    def __init__(self, startup, _a, _b, *, quit_application, shutdown_guard):
        self.startup, self.quit_application, self.shutdown_guard = (
            startup, quit_application, shutdown_guard)
        self.control = SimpleNamespace(shutdown=self.shutdown)
        self.exit_code = 0
        self.closed = 0

    def shutdown(self):
        self.shutdown_called = True

    def close(self):
        self.closed += 1


def install(monkeypatch, *, active=True, exit_code=0):
    app = Application([])
    app.result = 0
    termination = Termination()
    shutdown = Shutdown()
    effects = []
    monkeypatch.setattr("PyQt5.QtCore.QTimer", Timer)
    monkeypatch.setattr("PyQt5.QtWidgets.QApplication", Application)
    monkeypatch.setattr("sico.service.launch.prepare_project_state",
                        lambda path: effects.append(("prepare", path)))
    monkeypatch.setattr("sico_ui.wheel.prepare_wheel_environment",
                        lambda: effects.append(("wheel",)))
    monkeypatch.setattr("sico_ui.branding.install_logo",
                        lambda value: effects.append(("logo", value)))
    monkeypatch.setattr("sico_ui.qt_translation.install_chinese_qt_translations",
                        lambda value: effects.append(("translation", value)))
    monkeypatch.setattr("sico_ui.appearance_runtime.apply_project_appearance",
                        lambda path, application: effects.append(
                            ("appearance", path, application)))
    monkeypatch.setattr("sico_ui.startup.StartupView", View)
    monkeypatch.setattr("sico.service.frontend_startup.FrontendStartup", Startup)
    class Desktop:
        PARENT_EXIT_GRACE_SECONDS = Shutdown.PARENT_EXIT_GRACE_SECONDS

        def __new__(cls):
            return shutdown

    monkeypatch.setattr("sico.service.desktop.DesktopShutdown", Desktop)
    monkeypatch.setattr("sico.service.termination.TerminationSignal",
                        lambda: termination)
    Timer.instances.clear()
    Timer.single_shots.clear()
    args = SimpleNamespace(launch_dir="/project", session="" if active else None)

    def execute():
        if active:
            delay, callback = Timer.single_shots.pop(0)
            assert delay == 0
            callback()
            Timer.instances[0].timeout.emit()
        elif termination.received.is_set():
            Timer.instances[0].timeout.emit()
        return app.result

    app.exec_ = execute
    return args, app, effects, shutdown, termination


def test_run_local_gui_wraps_gui_input_once(monkeypatch):
    entered = []

    @contextmanager
    def gui_input(path):
        entered.append(path)
        yield

    monkeypatch.setattr("sico.service.gui_input.gui_input", gui_input)
    monkeypatch.setattr(local_startup, "_run_local_gui", lambda args: args.launch_dir)
    assert local_startup.run_local_gui(SimpleNamespace(launch_dir="/project")) == "/project"
    assert entered == ["/project"]


def test_run_local_gui_owns_event_loop_and_cleanup(monkeypatch):
    args, app, effects, shutdown, termination = install(monkeypatch, active=True)
    assert local_startup._run_local_gui(args) == 0
    assert args.session and len(args.session) == 32
    assert app.quit_on_close is False
    assert effects[0] == ("prepare", "/project")
    assert effects[1] == ("wheel",)
    assert shutdown.armed == [None]
    assert termination.restored == 1
    assert Timer.instances[0].interval == 50
    assert Timer.instances[0].started is True
    assert Timer.instances[0].stopped == 1
    assert app.aboutToQuit.disconnected == [shutdown.arm]


def test_run_local_gui_returns_startup_exit_code(monkeypatch):
    args, _app, _effects, shutdown, _termination = install(monkeypatch, active=True)
    import sico_ui.startup

    class ExitView(View):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.exit_code = 7

    monkeypatch.setattr(sico_ui.startup, "StartupView", ExitView)
    assert local_startup._run_local_gui(args) == 7


def test_parent_exit_stops_without_an_active_startup(monkeypatch):
    args, app, _effects, shutdown, termination = install(monkeypatch, active=False)
    termination.received.set()
    assert local_startup._run_local_gui(args) == 0
    assert app.quit_calls == 1
    assert shutdown.armed[0] == Shutdown.PARENT_EXIT_GRACE_SECONDS
    assert shutdown.armed[-1] is None
    assert len(args.session) == 32
