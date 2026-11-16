"""QTermWidget configuration is injected before shell startup and fully reversible."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from cad_ai_terminal import terminal_widget
from cad_ai_terminal.terminal_widget import (
    COLOR_SCHEME_ENV,
    DEFAULT_COLOR_SCHEME,
    SEARCH_BAR_LABEL,
    create_terminal_widget,
    localize_terminal_search_bar,
)


class Terminal:
    ScrollBarRight = 2
    schemes = ["BreezeModified"]
    key_bindings = "default"

    def __init__(self, _number, _parent):
        self.calls = []
        self.deleted = 0
        self.color = None
        self.cwd_seen = Path.cwd()

    @classmethod
    def availableColorSchemes(cls):
        return list(cls.schemes)

    def keyBindings(self):
        return self.key_bindings

    def setObjectName(self, value):
        self.calls.append(("object", value))

    def setProperty(self, name, value):
        self.calls.append(("property", name, value))

    def setShellProgram(self, value):
        self.calls.append(("program", value))

    def setArgs(self, value):
        self.calls.append(("args", value))

    def setWorkingDirectory(self, value):
        self.calls.append(("cwd", value))

    def setHistorySize(self, value):
        self.calls.append(("history", value))

    def setScrollBarPosition(self, value):
        self.calls.append(("scrollbar", value))

    def setTextCodec(self, value):
        self.calls.append(("codec", value))

    def setSizePolicy(self, horizontal, vertical):
        self.calls.append(("size-policy", horizontal, vertical))

    def setAutoClose(self, value):
        self.calls.append(("auto-close", value))

    def setTerminalFont(self, value):
        self.calls.append(("font", value))

    def setColorScheme(self, value):
        self.color = value

    def findChild(self, *_args):
        return None

    def deleteLater(self):
        self.deleted += 1


class Layout:
    def __init__(self):
        self.widgets = []

    def addWidget(self, widget, stretch):
        self.widgets.append((widget, stretch))


def make_qt():
    class QFont:
        pass

    class QFontDatabase:
        pass

    class QFontInfo:
        pass

    class QSizePolicy:
        Expanding = 1

    class QTextCodec:
        @staticmethod
        def codecForName(value):
            return ("codec", value)

    return {
        "QFont": QFont,
        "QFontDatabase": QFontDatabase,
        "QFontInfo": QFontInfo,
        "QSizePolicy": QSizePolicy,
        "QTextCodec": QTextCodec,
    }


def test_localize_ignores_missing_search_controls():
    class Empty:
        def findChild(self, *_args):
            return None

    localize_terminal_search_bar(Empty())
    assert SEARCH_BAR_LABEL == "查找："


def test_create_configures_terminal_from_keytab_directory_and_records_font(
    tmp_path, monkeypatch
):
    keytab = tmp_path / "kb" / "default.keytab"
    keytab.parent.mkdir()
    keytab.write_text("keytab", encoding="utf-8")
    scheme = tmp_path / "colors" / "custom.colorscheme"
    scheme.parent.mkdir()
    scheme.write_text("scheme", encoding="utf-8")
    monkeypatch.delenv(COLOR_SCHEME_ENV, raising=False)
    layout = Layout()
    before = Path.cwd()
    widget = create_terminal_widget(
        parent=None,
        number=4,
        layout=layout,
        qt=make_qt(),
        qtermwidget_type=Terminal,
        options=SimpleNamespace(program="bash", arguments=["-l"], workspace="/work"),
        resource_path=lambda name: keytab if name.endswith("default.keytab") else scheme,
        fixed_pitch_terminal_font=lambda *_args: "fixed-font",
    )
    assert Path.cwd() == before
    assert widget.cwd_seen == keytab.parent
    assert widget.color == str(scheme)
    assert ("object", "aiTerminal") in widget.calls
    assert ("property", "sessionNumber", 4) in widget.calls
    assert ("program", "bash") in widget.calls
    assert ("args", ["-l"]) in widget.calls
    assert ("cwd", "/work") in widget.calls
    assert ("history", 20000) in widget.calls
    assert ("scrollbar", 2) in widget.calls
    assert ("codec", ("codec", b"UTF-8")) in widget.calls
    assert ("size-policy", 1, 1) in widget.calls
    assert ("auto-close", True) in widget.calls
    assert ("font", "fixed-font") in widget.calls
    assert layout.widgets == [(widget, 1)]


@pytest.mark.parametrize(
    ("requested", "available", "expected"),
    [
        ("custom", ["custom"], "custom"),
        ("custom", [], None),
    ],
)
def test_color_scheme_falls_back_to_qtermwidget_names(
    tmp_path, monkeypatch, requested, available, expected
):
    keytab = tmp_path / "default.keytab"
    keytab.write_text("keytab", encoding="utf-8")
    monkeypatch.setenv(COLOR_SCHEME_ENV, requested)
    Terminal.schemes = available
    widget = create_terminal_widget(
        parent=None,
        number=1,
        layout=Layout(),
        qt=make_qt(),
        qtermwidget_type=Terminal,
        options=SimpleNamespace(program="bash", arguments=[], workspace="/work"),
        resource_path=lambda name: keytab if name.endswith("default.keytab") else None,
        fixed_pitch_terminal_font=lambda *_args: "font",
    )
    assert widget.color == expected
    assert DEFAULT_COLOR_SCHEME == "BreezeModified"


def test_keyboard_layout_failure_deletes_terminal_before_raising(tmp_path):
    class Broken(Terminal):
        key_bindings = "wrong"

    keytab = tmp_path / "default.keytab"
    keytab.write_text("keytab", encoding="utf-8")
    with pytest.raises(RuntimeError, match="keyboard layout"):
        create_terminal_widget(
            parent=None,
            number=1,
            layout=Layout(),
            qt=make_qt(),
            qtermwidget_type=Broken,
            options=SimpleNamespace(program="bash", arguments=[], workspace="/work"),
            resource_path=lambda name: keytab if name.endswith("default.keytab") else None,
            fixed_pitch_terminal_font=lambda *_args: "font",
        )
