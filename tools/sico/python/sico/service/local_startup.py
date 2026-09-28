"""Start the project-service GUI after the Qt event loop is running."""

import uuid


def run_local_gui(args):
    from sico.service.gui_input import gui_input

    with gui_input(args.launch_dir):
        return _run_local_gui(args)


def _run_local_gui(args):
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication

    from sico_ui.branding import install_logo
    from sico_ui.startup import StartupView
    from sico_ui.wheel import prepare_wheel_environment

    from .desktop import DesktopShutdown
    from .frontend_startup import FrontendStartup

    prepare_wheel_environment()
    app = QApplication.instance() or QApplication([])
    install_logo(app)
    app.setQuitOnLastWindowClosed(False)
    if not args.session:
        args.session = uuid.uuid4().hex
    shutdown = DesktopShutdown()
    active = []

    def start():
        startup = FrontendStartup(args, lambda *_args, **_kwargs: None)
        view = StartupView(startup, None, None, quit_application=app.quit, shutdown_guard=shutdown)
        active.append((startup, view))

    # The main event loop exists before any backend resource construction.
    QTimer.singleShot(0, start)
    app.aboutToQuit.connect(shutdown.arm)
    try:
        result = app.exec_()
        return (active[0][1].exit_code or result) if active else result
    finally:
        shutdown.arm()
        for startup, view in active:
            view.close()
            startup.wait(shutdown.remaining())
        app.aboutToQuit.disconnect(shutdown.arm)
