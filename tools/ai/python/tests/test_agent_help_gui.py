"""The far-right help button exposes the manual and version information."""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sico
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication
from sico_ui import help_menu
from sico_ui.home_window import HomeWindow


def test_help_button_sits_at_the_far_right_and_has_two_actions(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = HomeWindow(tmp_path, tmp_path / "workspace.ini", lambda _frontend: None)
    window.timer.stop()
    window.platform_menu.timer.stop()
    try:
        assert window.menuBar().cornerWidget(Qt.TopRightCorner) is window.help_button
        assert window.help_button.text() == "帮助"
        assert window.help_button.menu() is window.help_menu
        assert [action.text() for action in window.help_menu.actions()] == [
            "用户手册", "版本信息"]
    finally:
        window.finish()
        app.processEvents()


def test_help_actions_dispatch_manual_and_version_without_a_service(
        tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = HomeWindow(tmp_path, tmp_path / "workspace.ini", lambda _frontend: None)
    window.timer.stop()
    window.platform_menu.timer.stop()
    calls = []

    def record(kind):
        return lambda owner: calls.append((kind, owner))

    monkeypatch.setattr(help_menu, "show_user_manual", record("manual"))
    monkeypatch.setattr(help_menu, "show_version_info", record("version"))
    try:
        window.help_menu.actions()[0].trigger()
        window.help_menu.actions()[1].trigger()
        assert calls == [("manual", window), ("version", window)]
    finally:
        window.finish()
        app.processEvents()


def test_manual_path_is_the_reviewed_offline_html_entry():
    path = help_menu.manual_path()
    assert path is not None
    assert path.parts[-3:] == ("docs", "html", "index.html")
    assert path.is_file()


def test_browser_path_prefers_firefox(monkeypatch):
    monkeypatch.setattr(
        help_menu.shutil, "which",
        lambda name: "/usr/bin/firefox" if name == "firefox" else None,
    )
    assert help_menu.browser_path() == "/usr/bin/firefox"


def test_user_manual_opens_firefox_with_a_sanitized_environment(
        monkeypatch, tmp_path):
    page = tmp_path / "index.html"
    page.write_text("<html><body>manual</body></html>", encoding="utf-8")
    args_file = tmp_path / "browser-args"
    browser = tmp_path / "fake-firefox"
    browser.write_text(
        "#!/bin/sh\n"
        "{\n"
        "  for argument in \"$@\"; do printf 'arg=%s\\n' \"$argument\"; done\n"
        "  printf 'ld=%s\\n' \"${LD_LIBRARY_PATH-unset}\"\n"
        "} > \"$HELP_BROWSER_ARGS\"\n",
        encoding="ascii",
    )
    browser.chmod(0o755)
    notes = []
    owner = SimpleNamespace(info_app=SimpleNamespace(
        notice=lambda title, message: notes.append((title, message))))
    monkeypatch.setattr(help_menu, "manual_path", lambda: page)
    monkeypatch.setattr(help_menu, "browser_path", lambda: str(browser))
    monkeypatch.setenv("HELP_BROWSER_ARGS", str(args_file))
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bad/sico/lib")

    assert help_menu.show_user_manual(owner)
    deadline = time.monotonic() + 3
    while not args_file.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert args_file.read_text(encoding="utf-8").splitlines() == [
        "arg=--new-window",
        "arg=" + page.as_uri(),
        "ld=unset",
    ]
    assert notes == []


def test_version_information_is_product_and_runtime_only(monkeypatch):
    monkeypatch.setattr(
        help_menu, "current",
        lambda: SimpleNamespace(root=Path("/opt/sico")),
    )
    text = help_menu.version_text()
    assert sico.PRODUCT_NAME in text
    assert sico.__version__ in text
    assert "Python：" in text
    assert "Qt：" in text
    assert "/opt/sico" in text
    assert "模型" not in text and "Codex" not in text


def test_missing_manual_reports_without_starting_a_browser(monkeypatch):
    notes = []
    owner = SimpleNamespace(info_app=SimpleNamespace(
        notice=lambda title, message: notes.append((title, message))))
    monkeypatch.setattr(help_menu, "manual_path", lambda: None)
    assert not help_menu.show_user_manual(owner)
    assert notes and "未包含" in notes[0][1]


def test_missing_firefox_reports_without_falling_back_to_mime(monkeypatch, tmp_path):
    notes = []
    owner = SimpleNamespace(info_app=SimpleNamespace(
        notice=lambda title, message: notes.append((title, message))))
    monkeypatch.setattr(help_menu, "manual_path", lambda: tmp_path / "index.html")
    monkeypatch.setattr(help_menu, "open_manual", lambda _path: False)
    assert not help_menu.show_user_manual(owner)
    assert notes and "Firefox" in notes[0][1]
