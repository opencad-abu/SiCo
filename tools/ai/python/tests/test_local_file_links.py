"""Explicit local-file links open; inline highlights never become anchors."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtCore import QUrl
from PyQt5.QtWidgets import QApplication
from sico_ui import detail_navigation, local_links, workbench_navigation


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    application.processEvents()


class Notices:
    def __init__(self):
        self.warnings = []

    def warning(self, title, message):
        self.warnings.append((title, message))


class Ui:
    def __init__(self):
        self.notices = Notices()
        self.tabs = None
        self.conversation = object()


def chat_navigation():
    ui = Ui()
    centers = type("Centers", (), {"index": type(
        "Index", (), {"session_id": "session"})()})()
    return detail_navigation.DetailNavigation(
        ui, detail=None, centers=centers, page=None, presentation=None,
        show_panel=lambda _key: None, input_detail=None, event_detail=None,
    ), ui


def test_only_absolute_local_hrefs_are_classified_as_file_links(tmp_path):
    target = tmp_path / "package.json"
    target.write_text("{}", encoding="ascii")
    file_url = QUrl.fromLocalFile(str(target)).toString()
    assert local_links.local_file_path(str(target)) == target
    assert local_links.local_file_path(file_url) == target
    assert local_links.local_file_path("package.json") is None
    assert local_links.local_file_path("https://example.com/package.json") is None
    assert local_links.local_file_path(
        "copilot://data/session/record") is None
    assert local_links.local_file_path("") is None
    assert local_links.local_file_path(None) is None


def test_open_local_file_requires_a_regular_file_and_hands_it_to_qt(
        app, tmp_path, monkeypatch):
    target = tmp_path / "package.json"
    target.write_text("{}", encoding="ascii")
    opened = []
    desktop = type("Desktop", (), {
        "openUrl": staticmethod(lambda url: opened.append(url.toString()) or True),
    })
    monkeypatch.setattr(local_links, "QDesktopServices", desktop)

    assert local_links.open_local_file(target)
    assert opened == [QUrl.fromLocalFile(str(target)).toString()]
    assert not local_links.open_local_file(tmp_path)
    assert not local_links.open_local_file(tmp_path / "missing.json")


def test_chat_link_opens_the_local_file_but_missing_file_reports_a_notice(
        app, tmp_path, monkeypatch):
    target = tmp_path / "package.json"
    target.write_text("{}", encoding="ascii")
    opened = []
    monkeypatch.setattr(
        detail_navigation, "open_local_file",
        lambda path: opened.append(path) or path.exists(),
    )
    navigation, ui = chat_navigation()
    navigation.open_object_link(QUrl.fromLocalFile(str(target)))
    assert opened == [target]
    assert ui.notices.warnings == []

    missing = tmp_path / "missing.json"
    navigation.open_object_link(QUrl.fromLocalFile(str(missing)))
    assert ui.notices.warnings
    assert "文件不存在或系统无法打开" in ui.notices.warnings[0][1]


def test_detail_report_link_opens_the_local_file(app, tmp_path, monkeypatch):
    target = tmp_path / "package.json"
    target.write_text("{}", encoding="ascii")
    opened = []
    monkeypatch.setattr(
        workbench_navigation, "open_local_file",
        lambda path: opened.append(path) or True,
    )
    navigation = workbench_navigation.DetailNavigation()
    navigation.external_urls = set()
    notices = []
    navigation.show_notice = notices.append
    assert navigation.open_url(str(target))
    assert opened == [target]
    assert notices == []
