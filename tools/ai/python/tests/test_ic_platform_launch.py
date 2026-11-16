"""Launch plan resolution, the desktop launch wait, and menu wiring stay bounded."""

import os
import signal
import subprocess
import sys
import time
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_gui_wait import wait_until
from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QApplication
from sico.service.platform_launch import (
    CIW_LAYOUT_ENV,
    CIW_LAYOUT_LOWER_RIGHT,
    LaunchUnavailable,
    launch_plan,
    spawn_desktop,
)
from sico_ui.platform_launcher import PlatformLauncher


def _install(tmp_path, *, source):
    root = tmp_path / "install"
    (root / "scripts").mkdir(parents=True)
    if source:
        (root / "scripts/sicoAutoLoad.il").write_text("; bootstrap\n", encoding="utf-8")
    return root


def _project(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    return project


def _virtuoso(command, path=None):
    return "/usr/bin/virtuoso"


def _plan(tmp_path, project):
    return SimpleNamespace(argv=("/usr/bin/virtuoso",), cwd=str(project), environment={},
                           log_path=str(tmp_path / "launch.log"), bootstrap="")


def _future(value):
    future = Future()
    future.set_running_or_notify_cancel()
    if isinstance(value, Exception):
        future.set_exception(value)
    else:
        future.set_result(value)
    return future


class _Process:
    def __init__(self, code=None):
        self._code = code
        self.killed = False

    @property
    def returncode(self):
        return self._code

    def poll(self):
        return self._code

    def terminate(self):
        self.killed = True
        self._code = -15


class _Platforms:
    def __init__(self, pages):
        self.pages = pages
        self.calls = 0

    def instances(self):
        index = min(self.calls, len(self.pages) - 1)
        self.calls += 1
        return _future(self.pages[index])


def _launcher(tmp_path, pages, project=None, process=None, **options):
    project = project or _project(tmp_path)
    spawned = []
    launcher = PlatformLauncher(
        SimpleNamespace(platforms=_Platforms(pages)), project,
        plan_source=lambda target, **kwargs: _plan(tmp_path, target),
        spawn=lambda plan: spawned.append(plan) or (process or _Process()),
        timeout=options.pop("timeout", 5.0), interval=options.pop("interval", 20), **options)
    return launcher, project, spawned


def _pump(seconds):
    application = QApplication.instance()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.005)


def test_source_install_injects_canonical_bootstrap_and_sanitized_environment(tmp_path):
    root = _install(tmp_path, source=True)
    project = _project(tmp_path)
    environment = {"DISPLAY": ":9", "PATH": "/bin", "PYTHONPATH": "/leak",
                   "LD_PRELOAD": "leak.so", "QT_PLUGIN_PATH": "/qt", "SICO_HOME": "/other"}
    plan = launch_plan(project, environment=environment, root=root, which=_virtuoso)
    assert plan.argv == ("/usr/bin/virtuoso", "-restore",
                         str(root / "scripts/sicoAutoLoad.il"))
    assert plan.cwd == str(project)
    assert plan.bootstrap == str(root / "scripts/sicoAutoLoad.il")
    assert plan.environment["SICO_HOME"] == str(root)
    assert plan.environment[CIW_LAYOUT_ENV] == CIW_LAYOUT_LOWER_RIGHT
    assert plan.environment["DISPLAY"] == ":9"
    for name in ("PYTHONPATH", "LD_PRELOAD", "QT_PLUGIN_PATH"):
        assert name not in plan.environment
    assert Path(plan.log_path).parent.is_dir()


def test_binary_install_without_site_restore_does_not_inject(tmp_path):
    root = _install(tmp_path, source=False)
    plan = launch_plan(_project(tmp_path), environment={"DISPLAY": ":9", "PATH": "/bin"},
                       root=root, which=_virtuoso)
    assert plan.argv == ("/usr/bin/virtuoso",)
    assert plan.bootstrap == ""


def test_site_restore_environment_is_honoured(tmp_path):
    root = _install(tmp_path, source=False)
    restore = tmp_path / "site-restore.il"
    restore.write_text("; site\n", encoding="utf-8")
    environment = {"DISPLAY": ":9", "PATH": "/bin",
                   "SICO_VIRTUOSO_LAUNCH_RESTORE": str(restore)}
    plan = launch_plan(_project(tmp_path), environment=environment, root=root, which=_virtuoso)
    assert plan.argv == ("/usr/bin/virtuoso", "-restore", str(restore))


@pytest.mark.parametrize("kind", ["empty", "relative", "missing"])
def test_invalid_site_restore_is_refused(tmp_path, kind):
    root = _install(tmp_path, source=False)
    project = _project(tmp_path)
    value = {"empty": "", "relative": "restore.il",
             "missing": str(tmp_path / "missing.il")}[kind]
    environment = {"DISPLAY": ":9", "PATH": "/bin",
                   "SICO_VIRTUOSO_LAUNCH_RESTORE": value}
    with pytest.raises(LaunchUnavailable) as refused:
        launch_plan(project, environment=environment, root=root, which=_virtuoso)
    assert "SICO_VIRTUOSO_LAUNCH_RESTORE" in str(refused.value)


def test_missing_virtuoso_and_display_are_refused(tmp_path):
    root = _install(tmp_path, source=True)
    project = _project(tmp_path)
    with pytest.raises(LaunchUnavailable) as refused:
        launch_plan(project, environment={"DISPLAY": ":9", "PATH": "/bin"}, root=root,
                    which=lambda command, path=None: None)
    assert "virtuoso" in str(refused.value)
    with pytest.raises(LaunchUnavailable) as refused:
        launch_plan(project, environment={"PATH": "/bin"}, root=root, which=_virtuoso)
    assert "DISPLAY" in str(refused.value)


def test_spawn_desktop_writes_a_private_launch_log(tmp_path):
    plan = SimpleNamespace(argv=(sys.executable, "-c", "print('started')"), cwd=str(tmp_path),
                           environment=dict(os.environ), log_path=str(tmp_path / "launch.log"),
                           bootstrap="")
    process = spawn_desktop(plan)
    assert process.wait(timeout=20) == 0
    assert "started" in Path(plan.log_path).read_text(encoding="utf-8")
    assert (Path(plan.log_path).stat().st_mode & 0o077) == 0


def test_spawned_platform_survives_the_sico_process_that_started_it(tmp_path):
    marker = tmp_path / "platform.pid"
    script = "\n".join([
        "import os, sys",
        "from pathlib import Path",
        "from types import SimpleNamespace",
        "from sico.service.platform_launch import spawn_desktop",
        "plan = SimpleNamespace(argv=(sys.executable, '-c', 'import time; time.sleep(60)'),",
        "                       cwd=sys.argv[1], environment=dict(os.environ),",
        "                       log_path=sys.argv[2], bootstrap='')",
        "process = spawn_desktop(plan)",
        "Path(sys.argv[3]).write_text(str(process.pid))",
    ])
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), str(tmp_path / "launch.log"),
         str(marker)],
        env=environment, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    pid = int(marker.read_text())
    try:
        assert os.kill(pid, 0) is None
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_launcher_reports_the_new_candidate_without_killing_the_desktop(tmp_path):
    project = _project(tmp_path)
    existing = dict(id="a" * 64, project=str(project))
    fresh = dict(id="b" * 64, project=str(project))
    process = _Process()
    launcher, _project_path, spawned = _launcher(
        tmp_path, [[existing], [existing, fresh]], project=project, process=process)
    seen = []
    launcher.registered.connect(seen.append)
    launcher.start()
    wait_until(lambda: len(spawned) == 1)
    assert launcher.active
    wait_until(lambda: bool(seen))
    assert seen == ["b" * 64]
    assert not launcher.active and not process.killed


def test_launcher_fails_when_the_desktop_exits_early(tmp_path):
    launcher, _project_path, spawned = _launcher(tmp_path, [[]], process=_Process(code=7))
    failures = []
    launcher.failed.connect(failures.append)
    launcher.start()
    wait_until(lambda: bool(failures))
    assert "提前退出" in failures[0] and "launch.log" in failures[0]
    assert not launcher.active and len(spawned) == 1


def test_launcher_times_out_without_registration(tmp_path):
    launcher, _project_path, spawned = _launcher(tmp_path, [[]], timeout=0.15, interval=20)
    failures = []
    launcher.failed.connect(failures.append)
    launcher.start()
    wait_until(lambda: bool(failures))
    assert "超时" in failures[0] and "launch.log" in failures[0]
    assert not launcher.active and len(spawned) == 1


def test_launcher_refuses_before_spawning_when_the_baseline_fails(tmp_path):
    launcher, _project_path, spawned = _launcher(tmp_path, [RuntimeError("service gone")])
    failures = []
    launcher.failed.connect(failures.append)
    launcher.start()
    wait_until(lambda: bool(failures))
    assert "未启动" in failures[0] and not spawned


def test_cancel_stops_waiting_without_signals_or_termination(tmp_path):
    process = _Process()
    launcher, _project_path, spawned = _launcher(tmp_path, [[]], process=process)
    seen, failures = [], []
    launcher.registered.connect(seen.append)
    launcher.failed.connect(failures.append)
    launcher.start()
    wait_until(lambda: len(spawned) == 1)
    launcher.cancel()
    _pump(0.3)
    assert not launcher.active and not seen and not failures
    assert not process.killed and len(spawned) == 1


def test_menu_launch_waits_then_connects_without_a_session(tmp_path, monkeypatch):
    from agent_service_business_fixtures import business_service
    from sico.service.remote_frontend import RemoteFrontend
    from sico_ui.home_window import HomeWindow

    started = []

    class StubLauncher(QObject):
        registered = pyqtSignal(str)
        failed = pyqtSignal(str)

        def __init__(self, api, project, *, parent=None):
            super().__init__(parent)
            self.cancelled = False

        def start(self):
            started.append(self)

        def cancel(self):
            self.cancelled = True

    monkeypatch.setattr("sico_ui.platform_menu.PlatformLauncher", StubLauncher)
    with business_service(tmp_path, monkeypatch) as service:
        api = RemoteFrontend(service.descriptor, tmp_path, "")
        window = HomeWindow(str(tmp_path), tmp_path / "workspace.ini", lambda _frontend: None)
        notices, warnings = [], []
        monkeypatch.setattr(window.info_app, "notice",
                            lambda title, message: notices.append((title, message)))
        monkeypatch.setattr(window.info_app, "warning",
                            lambda title, message: warnings.append((title, message)))
        try:
            window.show()
            window.ready(api)
            wait_until(lambda: api.connection_state == "connected")
            assert window.launch_button.isEnabled()
            connected = []

            def connect(candidate):
                connected.append(candidate)
                window.platform_menu.refresh()

            monkeypatch.setattr(window.platform_menu, "_start", connect)
            window.platform_menu.launch_action.trigger()
            stub = window.platform_menu.launcher
            assert started == [stub] and not window.platform_menu.launch_action.isEnabled()
            assert window.platform_menu.cancel_action.text() == "取消启动"
            stub.registered.emit("c" * 64)
            assert connected == ["c" * 64] and window.platform_menu.launcher is None

            window.platform_menu.launch_action.trigger()
            stub = window.platform_menu.launcher
            window.platform_menu.cancel_action.trigger()
            assert stub.cancelled and window.platform_menu.launcher is None
            assert notices[-1][0] == "已停止等待启动"

            window.platform_menu.launch_action.trigger()
            stub = window.platform_menu.launcher
            stub.failed.emit("boom")
            assert warnings[-1][0] == "CDNS-IC 启动未完成"
            assert window.platform_menu.launcher is None
        finally:
            window.finish()
            window.deleteLater()
            api.close_desktop()
            wait_until(lambda: api.wait_desktop(0))
