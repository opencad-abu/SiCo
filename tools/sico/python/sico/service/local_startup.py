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
    from sico_ui.appearance_runtime import apply_project_appearance
    from sico_ui.qt_translation import install_chinese_qt_translations
    from sico_ui.startup import StartupView
    from sico_ui.wheel import prepare_wheel_environment

    from .desktop import DesktopShutdown
    from .frontend_startup import FrontendStartup
    from .launch import prepare_project_state
    from .termination import TerminationSignal

    prepare_project_state(args.launch_dir)
    prepare_wheel_environment()
    app = QApplication.instance() or QApplication([])
    install_logo(app)
    install_chinese_qt_translations(app)
    apply_project_appearance(args.launch_dir, application=app)
    app.setQuitOnLastWindowClosed(False)
    if not args.session:
        args.session = uuid.uuid4().hex
    shutdown = DesktopShutdown()
    termination = TerminationSignal().install()
    active = []

    def start():
        startup = FrontendStartup(args, lambda *_args, **_kwargs: None)
        view = StartupView(startup, None, None, quit_application=app.quit, shutdown_guard=shutdown)
        active.append((startup, view))

    # The main event loop exists before any backend resource construction.
    QTimer.singleShot(0, start)
    app.aboutToQuit.connect(shutdown.arm)
    termination_timer = QTimer()
    termination_timer.setInterval(50)

    def parent_exit():
        if termination.received.is_set():
            termination_timer.stop()
            shutdown.arm(DesktopShutdown.PARENT_EXIT_GRACE_SECONDS)
            if active:
                for _startup, view in tuple(active):
                    view.control.shutdown()
            else:
                app.quit()

    termination_timer.timeout.connect(parent_exit)
    termination_timer.start(50)
    try:
        result = app.exec_()
        return (active[0][1].exit_code or result) if active else result
    finally:
        termination_timer.stop()
        termination.restore()
        shutdown.arm()
        for startup, view in active:
            view.close()
            startup.wait(shutdown.remaining())
        app.aboutToQuit.disconnect(shutdown.arm)
