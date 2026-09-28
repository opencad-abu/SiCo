"""Agent/PyQt child; control commands use a private parent pipe, SKILL uses TCP."""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

from ..core.contracts import NeedsReconcile
from ..providers.config import provider_config_path
from ..transport.framing import strict_json
from ..transport.relay import StdioLines
from .frontend_startup import FrontendStartup
from .notices import HostNotices


class DesktopShutdown:
    """Bound the whole desktop's explicit shutdown, including Qt and cleanup."""

    GRACE_SECONDS = 5.0

    def __init__(self):
        self.deadline = None
        self._lock = threading.Lock()

    def arm(self):
        with self._lock:
            if self.deadline is not None:
                return
            self.deadline = time.monotonic() + self.GRACE_SECONDS
            threading.Thread(target=self._expire, name="desktop-exit", daemon=True).start()

    def remaining(self):
        return max(0, self.deadline - time.monotonic())

    def _expire(self):
        threading.Event().wait(self.remaining())
        # Also bound interpreter shutdown waiting for non-daemon handlers.
        # A full diagnostic pipe must not delay the final process exit.
        try:
            os.set_blocking(2, False)
            os.write(2, b"Desktop shutdown deadline expired; forcing process exit\n")
        except OSError:
            pass
        finally:
            os._exit(2)


def resume_context(broker, current, saved):
    """Reuse a registered source, or reconnect history to the same live project."""
    try:
        registered = broker.context(saved.instance_id, saved.target_id, timeout=0)
        if registered.record() == saved.record():
            return registered
    except (NeedsReconcile, TimeoutError):
        pass
    old_cwd, cwd = saved.snapshot.get("cwd"), current.snapshot.get("cwd")
    try:
        same_project = bool(old_cwd and cwd and Path(old_cwd).samefile(cwd))
    except OSError:
        same_project = False
    if not same_project:
        raise ValueError("历史会话属于另一工程，请从该工程打开 Silicon Copilot 后继续")
    return current


def configured_provider_path(launch_dir, explicit=None, environment=None):
    return provider_config_path(launch_dir, explicit, environment)


def run_desktop(args):
    from sico.service.gui_input import gui_input

    with gui_input(args.launch_dir):
        return _run_desktop(args)


def _run_desktop(args):
    from PyQt5.QtWidgets import QApplication

    from sico_ui.branding import install_logo
    from sico_ui.startup import StartupView
    from sico_ui.wheel import prepare_wheel_environment

    prepare_wheel_environment()
    app = QApplication.instance() or QApplication([])
    install_logo(app)
    app.setQuitOnLastWindowClosed(False)
    controls = StdioLines(sys.stdin.buffer, decode=strict_json)
    notice = HostNotices(sys.stdout.fileno())
    shutdown = DesktopShutdown()
    startup = FrontendStartup(args, notice)
    view = StartupView(startup, controls, notice, quit_application=app.quit,
                       shutdown_guard=shutdown)
    app.aboutToQuit.connect(shutdown.arm)
    app.aboutToQuit.connect(view.close)
    try:
        result = app.exec_()
        return 2 if notice.failed.is_set() else (view.exit_code or result)
    finally:
        shutdown.arm()
        view.close()
        # Drain notices and services concurrently within the original deadline.
        notice.close(timeout=shutdown.remaining())
        startup.wait(shutdown.remaining())
        notice.wait(shutdown.remaining())
        app.aboutToQuit.disconnect(view.close)
        app.aboutToQuit.disconnect(shutdown.arm)
