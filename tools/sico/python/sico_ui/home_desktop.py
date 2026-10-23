"""Run the standalone project home before any IC platform or model is selected."""

from pathlib import Path


def run_home(args):
    from sico.service.gui_input import gui_input

    with gui_input(args.launch_dir):
        return _run_home(args)


def _run_home(args):
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication

    from sico_ui.branding import install_logo
    from sico_ui.appearance_runtime import apply_saved_appearance
    from sico_ui.home_window import HomeWindow
    from sico_ui.qt_translation import install_chinese_qt_translations
    from sico_ui.receipts import DataReceipt
    from sico_ui.service_connection import attach_service_connection
    from sico_ui.wheel import prepare_wheel_environment
    from sico_ui.window import AssistantWindow
    from sico.service.desktop import DesktopShutdown
    from sico.service.home_startup import HomeStartup
    from sico.service.termination import TerminationSignal
    from sico.service.launch import prepare_project_state

    prepare_project_state(args.launch_dir)
    prepare_wheel_environment()
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    install_logo(app)
    install_chinese_qt_translations(app)
    workspace_path = Path(args.launch_dir) / ".sico/ai/agent/workspace.ini"
    apply_saved_appearance(workspace_path, application=app)
    shutdown = DesktopShutdown()
    termination = TerminationSignal().install()
    startup = None
    session_window = None

    def opened(frontend):
        nonlocal session_window
        if home.lifecycle.closing:
            return
        session_window = AssistantWindow(frontend)
        attach_service_connection(session_window)
        session_window.lifecycle.attach_guard(shutdown)
        session_window.closed.connect(app.quit)
        session_window.setGeometry(home.geometry())
        home.timer.stop()
        home.platform_menu.close()
        home.hide()
        session_window.restore()

    home = HomeWindow(args.launch_dir, workspace_path, opened, args=args)
    home.closed.connect(app.quit)
    receipt = DataReceipt(home)

    def start():
        nonlocal startup
        if home.lifecycle.closing:
            return
        if startup is not None:
            startup.close()
            home.api = None
            home.connect_button.setEnabled(False)
            home.launch_button.setEnabled(False)
            home.settings_button.setEnabled(False)
            home.settings_action.setEnabled(False)
            home.stop_action.setEnabled(False)
            home.retry_action.setEnabled(False)
            home.service_label.setText("正在重新发现项目服务…")
            if not startup.wait(0):
                QTimer.singleShot(50, start)
                return
        startup = HomeStartup(args, lambda *_a, **_k: None)
        receipt.watch(startup.ready, home.ready, home.failed)

    home.retry = start
    home.show()
    QTimer.singleShot(0, start)
    app.aboutToQuit.connect(shutdown.arm)
    termination_timer = QTimer()
    termination_timer.setInterval(50)

    def parent_exit():
        if termination.received.is_set():
            termination_timer.stop()
            shutdown.arm(DesktopShutdown.PARENT_EXIT_GRACE_SECONDS)
            app.quit()

    termination_timer.timeout.connect(parent_exit)
    termination_timer.start(50)
    try:
        return app.exec_()
    finally:
        termination_timer.stop()
        termination.restore()
        shutdown.arm()
        receipt.clear()
        home.platform_menu.close()
        if session_window is not None:
            session_window.api.close_desktop()
        if startup is not None:
            startup.close()
            startup.wait(shutdown.remaining())
        home.lifecycle.closing = True
        home.close()
        app.aboutToQuit.disconnect(shutdown.arm)
