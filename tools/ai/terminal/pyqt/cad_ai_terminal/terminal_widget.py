"""Configure one QTermWidget before its shell is started."""

from __future__ import annotations

import os
from pathlib import Path

from sicoenv import read as environment_setting


COLOR_SCHEME_ENV = "SICO_AI_TERMINAL_COLOR_SCHEME"
DEFAULT_COLOR_SCHEME = "BreezeModified"


def create_terminal_widget(
    parent,
    number,
    layout,
    *,
    qt,
    qtermwidget_type,
    options,
    resource_path,
    fixed_pitch_terminal_font,
):
    QFont = qt["QFont"]
    QFontDatabase = qt["QFontDatabase"]
    QFontInfo = qt["QFontInfo"]
    QSizePolicy = qt["QSizePolicy"]
    QTextCodec = qt["QTextCodec"]
    previous = Path.cwd()
    key_binding = resource_path("kb-layouts/default.keytab")
    try:
        if key_binding is not None:
            os.chdir(str(key_binding.parent))
        terminal = qtermwidget_type(0, parent)
    finally:
        os.chdir(str(previous))
    if terminal.keyBindings() != "default":
        terminal.deleteLater()
        raise RuntimeError("cannot load the bundled QTermWidget keyboard layout")

    terminal.setObjectName("aiTerminal")
    terminal.setProperty("sessionNumber", number)
    terminal.setShellProgram(str(options.program))
    terminal.setArgs(list(options.arguments))
    terminal.setWorkingDirectory(str(options.workspace))
    terminal.setHistorySize(20000)
    terminal.setScrollBarPosition(qtermwidget_type.ScrollBarRight)
    terminal.setTextCodec(QTextCodec.codecForName(b"UTF-8"))
    terminal.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    terminal.setAutoClose(True)
    font = fixed_pitch_terminal_font(QFontDatabase, QFont, QFontInfo)
    terminal.setTerminalFont(font)
    # The embedded SiCo tools keep the product palette; unset keeps the
    # shared AI Assistant terminal on its own default scheme.
    scheme_name = environment_setting(os.environ, COLOR_SCHEME_ENV, "").strip() or DEFAULT_COLOR_SCHEME
    scheme = resource_path(f"color-schemes/{scheme_name}.colorscheme")
    if scheme is not None:
        terminal.setColorScheme(str(scheme))
    elif scheme_name in qtermwidget_type.availableColorSchemes():
        terminal.setColorScheme(scheme_name)
    layout.addWidget(terminal, 1)
    return terminal
