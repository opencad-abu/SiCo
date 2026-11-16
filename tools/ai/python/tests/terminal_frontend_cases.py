"""Terminal frontend regressions."""

from __future__ import annotations

from terminal_fixtures import (
    PYQT_ROOT,
    terminal_session,
)


def test_frontend_resource_lookup_has_one_public_owner():
    source = (PYQT_ROOT / "cad_ai_terminal" / "resources.py").read_text()
    assert "from sicoresources import terminal_resource" in source
    assert "from sicoresources import icon" in source
    assert "terminal_resource(relative)" in source
    assert 'icon("brand", "logo.png")' in source


def test_frontend_explicitly_releases_qtermwidget_on_exit() -> None:
    source = (PYQT_ROOT / "cad_ai_terminal" / "terminal_session.py").read_text(encoding="utf-8")

    assert "terminal.close()" in source
    assert "terminal.deleteLater()" in source
    assert "self._release_terminal()" in source


def test_terminal_shell_environment_drops_loader_variables() -> None:
    environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/site",
        "LD_LIBRARY_PATH": "/software/pkgs/python/3.9.13/lib",
        "LD_PRELOAD": "/opt/preload.so",
        "LD_AUDIT": "/opt/audit.so",
        "PYTHONHOME": "/python",
        "PYTHONPATH": "/runtime/python",
        "PYTHONNOUSERSITE": "1",
        "CAD_AI_TOKEN": "session-value",
    }

    terminal_session.sanitize_shell_environment(environment)

    assert environment["PATH"] == "/usr/bin:/bin"
    assert environment["CAD_AI_TOKEN"] == "session-value"
    for name in (
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "LD_AUDIT",
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONNOUSERSITE",
    ):
        assert name not in environment


def test_terminal_shell_starts_without_the_frontend_loader_paths() -> None:
    source = (PYQT_ROOT / "cad_ai_terminal" / "terminal_session.py").read_text(encoding="utf-8")

    assert "sanitize_shell_environment()" in source
    assert source.index("sanitize_shell_environment()") < source.index(
        "terminal.startShellProgram()"
    )
    assert "terminal.setEnvironment(" not in source


def test_frontend_right_click_does_not_cross_a_python_signal_slot() -> None:
    source = (PYQT_ROOT / "cad_ai_terminal" / "session_context.py").read_text(encoding="utf-8")

    assert "setContextMenuPolicy(Qt.ActionsContextMenu)" in source
    assert '"新建会话"' in source
    assert ".customContextMenuRequested.connect" not in source
    assert ".aboutToShow.connect" not in source


def test_frontend_installs_bounded_independent_session_tabs() -> None:
    source = (PYQT_ROOT / "cad_ai_terminal" / "terminal_window.py").read_text(encoding="utf-8")
    session_source = (PYQT_ROOT / "cad_ai_terminal" / "session_context.py").read_text(
        encoding="utf-8"
    )

    assert "QTabWidget" in source
    assert "MAXIMUM_SESSIONS = 8" in source
    assert "setTabsClosable(True)" in source
    assert "setMovable(True)" in source
    rename_source = (PYQT_ROOT / "cad_ai_terminal/tab_renamer.py").read_text()
    assert '"重命名会话"' in rename_source
    assert 'QKeySequence("F2")' in rename_source
    assert "tabBarDoubleClicked.connect" in rename_source
    assert 'QKeySequence("Ctrl+Shift+T")' in session_source


def test_frontend_stops_all_tabs_before_releasing_qtermwidgets() -> None:
    source = (PYQT_ROOT / "cad_ai_terminal" / "window_shutdown.py").read_text(encoding="utf-8")
    session_source = (PYQT_ROOT / "cad_ai_terminal" / "terminal_session.py").read_text(
        encoding="utf-8"
    )

    begin = source.index("def begin")
    force = source.index("def _force")
    release = source.index("def _finish")
    assert begin < force < release
    assert "session.begin_shutdown()" in source[begin:force]
    assert "session.force_shutdown()" in source[force:release]
    assert "self._release_sessions()" in source[release:]
    assert "os.kill(process_id, signum)" in session_source


def test_frontend_resyncs_the_ibus_cursor_after_terminal_output() -> None:
    session_source = (PYQT_ROOT / "cad_ai_terminal" / "terminal_session.py").read_text(
        encoding="utf-8"
    )

    assert "terminal.receivedData.connect(self._queue_input_method_cursor_update)" in (
        session_source
    )
    input_source = (PYQT_ROOT / "cad_ai_terminal/input_behavior.py").read_text()
    assert "application_type.inputMethod().update(qt.ImCursorRectangle)" in input_source
    assert terminal_session.INPUT_METHOD_CURSOR_SYNC_MS >= 40


def test_terminal_toolbar_and_search_bar_are_chinese(qt_application_lifetime):
    from cad_ai_terminal.terminal_widget import localize_terminal_search_bar
    from cad_ai_terminal.toolbar import TerminalToolbar
    from cadgui.palette import active
    from PyQt5.QtWidgets import QAction, QLabel, QMenu, QToolButton, QWidget

    toolbar = TerminalToolbar(
        None,
        "/workspace",
        copy=lambda: None,
        paste=lambda: None,
        find=lambda: None,
        zoom_out_callback=lambda: None,
        zoom_in_callback=lambda: None,
        interrupt_callback=lambda: None,
    )
    assert toolbar.windowTitle() == "终端"
    assert toolbar.findChild(QLabel, "sessionStatus").text().strip() == "启动中"
    assert toolbar.findChild(QLabel, "workspaceLabel").text() == "工作区："
    assert [
        toolbar.findChild(QAction, name).text()
        for name in ("copyAction", "pasteAction", "searchAction")
    ] == ["复制", "粘贴", "查找"]
    status = toolbar.findChild(QLabel, "sessionStatus")
    toolbar.set_status("运行中", active().state_ready)
    assert active().on_accent in status.styleSheet()
    assert active().state_ready in status.styleSheet()

    terminal = QWidget()
    label = QLabel(terminal)
    label.setObjectName("findLabel")
    label.setText("Find:")
    options = QToolButton(terminal)
    options.setObjectName("optionsButton")
    menu = QMenu(options)
    for text in ("Match case", "Regular expression", "Highlight all matches"):
        menu.addAction(text)
    options.setMenu(menu)
    localize_terminal_search_bar(terminal)
    assert label.text() == "查找："
    assert [action.text() for action in menu.actions()] == [
        "区分大小写",
        "正则表达式",
        "高亮所有匹配",
    ]


def test_real_qtermwidget_context_menu_stays_inside_qt() -> None:
    from terminal_smoke_support import run_real_terminal_smoke

    run_real_terminal_smoke("offscreen")
