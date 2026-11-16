"""Appearance preferences, live palette application and persisted font roles."""

from __future__ import annotations

import configparser
import io
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from cadgui.appearance import application_palette
from cadgui.palette import DARK, LIGHT
from cadgui.theme import window_stylesheet
from PyQt5.QtCore import QByteArray, QSettings
from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import QApplication, QLabel, QTextBrowser, QTreeWidget, QTreeWidgetItem
from sico import PRODUCT_NAME
from sico_ui.appearance import (
    THEME_KEY,
    AppearancePreferences,
    load_appearance,
    save_appearance,
)
from sico_ui.appearance_runtime import apply_appearance
from sico_ui.branding import logo_icon, logo_path
from sico_ui.code_highlight import code_block_html
from sico_ui.home_window import HomeWindow
from sico_ui.presentation import DocumentView
from sico_ui.row_stripes import enable_row_stripes
from sico_ui.theme import window_stylesheet as active_window_stylesheet
from sico_ui.transcript import Transcript
from sico_ui.workspace_settings import open_settings, validate_stream


def luminance(value):
    channels = [int(value[index:index + 2], 16) / 255
                for index in (1, 3, 5)]
    channels = [channel / 12.92 if channel <= 0.04045
                else ((channel + 0.055) / 1.055) ** 2.4
                for channel in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(foreground, background):
    high, low = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.fixture
def application():
    app = QApplication.instance() or QApplication([])
    point = app.font().pointSize()
    try:
        yield app
    finally:
        apply_appearance(AppearancePreferences(
            "light", point if point > 0 else 10, point if point > 0 else 10))


def test_dark_palette_keeps_accent_and_has_readable_warm_contrast():
    assert DARK.accent == LIGHT.accent
    assert DARK.background != LIGHT.background
    assert contrast(DARK.text, DARK.background) >= 12
    stylesheet = window_stylesheet(DARK)
    assert f"background-color: {DARK.background}" in stylesheet
    assert f"color: {DARK.text}" in stylesheet
    assert f"color: {DARK.accent}" in stylesheet
    assert f"color: {LIGHT.text}" not in stylesheet


def test_dark_code_number_is_deeper_blue_with_readable_contrast():
    assert DARK.code_number == "#64B5F6"
    assert luminance(DARK.code_number) < 0.45
    assert contrast(DARK.code_number, DARK.code_block_background) >= 6.5


def test_dark_links_are_vivid_azure_and_render_in_transcript(application):
    assert DARK.link == "#66B2FF"
    assert contrast(DARK.link, DARK.background) >= 7.5
    assert application_palette(DARK).color(QPalette.Link).name().lower() == \
        DARK.link.lower()
    apply_appearance(AppearancePreferences("dark", 14, 11), force=True)
    transcript = Transcript(username="tester")
    transcript.append(
        "report", "只读检查当前 Virtuoso 窗口",
        result={"url": "event-detail:1", "version": 1},
    )
    view = DocumentView()
    view.resize(510, 120)
    view.show()
    try:
        transcript.render(view, force=True)
        application.processEvents()
        cursor = view.document().find("只读检查当前 Virtuoso 窗口")
        assert cursor.charFormat().foreground().color().name().lower() == \
            DARK.link.lower()
    finally:
        view.close()
        view.deleteLater()
        application.processEvents()


def test_dialog_saves_appearance_and_updates_label_and_document_fonts(
        application, tmp_path):
    settings_path = tmp_path / "workspace.ini"
    window = HomeWindow(tmp_path, settings_path, lambda _frontend: None)
    window.timer.stop()
    window.platform_menu.timer.stop()
    try:
        assert window.appearance_action.isEnabled()
        assert window.history.alternatingRowColors()
        assert window.history.property("stripeRows") is True
        window.appearance_action.trigger()
        dialog = window.appearance_settings_dialog
        assert dialog.windowTitle() == "外观设置"
        assert dialog.theme.currentData() == "dark"
        dialog.theme.setCurrentIndex(dialog.theme.findData("dark"))
        dialog.text_size.setValue(16)
        dialog.label_size.setValue(10)
        assert dialog.grab().save(str(tmp_path / "appearance-settings.png"))
        dialog.save()
        assert dialog.saved
        stored = load_appearance(open_settings(settings_path))
        assert stored == AppearancePreferences("dark", 16, 10)
        assert window.appearance == stored
        assert application.font().pointSize() == 10
        assert application.palette().window().color().name().lower() == \
            DARK.background.lower()
        assert DARK.background in window.styleSheet()
        label = QLabel("标签", window)
        document = QTextBrowser(window)
        label.show()
        document.show()
        application.processEvents()
        assert label.font().pointSize() == 10
        assert document.font().pointSize() == 16
    finally:
        window.finish()
        application.processEvents()


@pytest.mark.parametrize("palette", (LIGHT, DARK))
def test_session_history_rows_alternate_with_the_theme_stripe(
        application, palette):
    apply_appearance(AppearancePreferences(
        palette.name, 14, 10), force=True)
    tree = enable_row_stripes(QTreeWidget())
    tree.setStyleSheet(active_window_stylesheet())
    tree.resize(320, 180)
    tree.show()
    for index in range(4):
        tree.addTopLevelItem(QTreeWidgetItem([f"会话 {index}", "状态"]))
    application.processEvents()
    image = tree.viewport().grab().toImage()
    colors = [
        image.pixelColor(6, tree.visualItemRect(tree.topLevelItem(index)).center().y()).name()
        for index in range(4)
    ]
    assert colors == [palette.background.lower(), palette.stripe.lower()] * 2
    tree.close()
    application.processEvents()


def test_session_window_navigation_lists_enable_stripes(application, tmp_path):
    from agent_window_fixture import create_window
    from sico.core.contracts import BoundContext
    from sico.core.tools import ToolRegistry
    from sico.providers.mock import ContextDemoProvider
    from sico.service.python_backend import PythonBackend
    from sico.storage.journal import SessionJournal

    with SessionJournal(tmp_path, "stripes") as journal:
        loop = PythonBackend(
            ContextDemoProvider(), ToolRegistry(), journal,
            BoundContext("stripes-instance", "stripes-generation", "bound", {"valid": True}),
        )
        window = create_window(loop)
        try:
            for tree in (window.navigation.current, window.navigation.list):
                assert tree.alternatingRowColors()
                assert tree.property("stripeRows") is True
        finally:
            window.close()
            application.processEvents()


def test_status_footer_keeps_both_rows_visible_after_label_size_changes(
        application, tmp_path):
    from agent_window_fixture import create_window
    from sico.core.contracts import BoundContext
    from sico.core.tools import ToolRegistry
    from sico.providers.mock import ContextDemoProvider
    from sico.service.python_backend import PythonBackend
    from sico.storage.journal import SessionJournal

    class CodexLike(ContextDemoProvider):
        base = "codex-0.162.1"
        model = "deepseek-v4-flash"
        label = "Codex 0.162.1 / deepseek-v4-flash / native"

    with SessionJournal(tmp_path, "footer") as journal:
        loop = PythonBackend(
            CodexLike(), ToolRegistry(), journal,
            BoundContext("footer-instance", "footer-generation", "bound", {"valid": True}),
        )
        window = create_window(loop)
        platform = QLabel("CDNS-IC · 已连接 · instance-0123456789")
        platform.setObjectName("platformStatus")
        window.add_platform_status(platform)
        service = QLabel("服务：已连接")
        window.add_service_status(service)
        window.show()
        for _ in range(3):
            application.processEvents()
        try:
            for size in (20, 8, 20, 8):
                apply_appearance(AppearancePreferences(
                    "light", 14, size), force=True)
                for _ in range(3):
                    application.processEvents()
                assert platform.geometry().bottom() < window.status.geometry().top()
                assert window.status.geometry().bottom() <= \
                    window.status_box.rect().bottom()
                assert window.model_status.height() >= \
                    window.model_status.fontMetrics().height() * 2
                assert window.model_status.font().pointSize() == 6
                assert platform.font() == window.model_status.font()
                assert window.status.font() == window.model_status.font()
                assert service.font() == window.model_status.font()
        finally:
            window.close()
            application.processEvents()


def test_titlebar_logo_and_window_icon_follow_the_theme(application, tmp_path):
    window = HomeWindow(tmp_path, tmp_path / "workspace.ini", lambda _frontend: None)
    window.timer.stop()
    window.platform_menu.timer.stop()
    try:
        for theme, asset in (
            ("light", "logo-light.png"),
            ("dark", "logo-dark.png"),
            ("light", "logo-light.png"),
        ):
            apply_appearance(AppearancePreferences(
                theme, 14, 10), force=True)
            assert window.windowTitle() == PRODUCT_NAME
            assert Path(logo_path()).name == asset
            assert window.title_bar.logo.pixmap().toImage() == \
                logo_icon().pixmap(20, 20).toImage()
            assert window.windowIcon().cacheKey() == logo_icon().cacheKey()
    finally:
        window.finish()
        application.processEvents()


def test_dark_theme_recolors_retained_transcript(application):
    apply_appearance(AppearancePreferences("dark", 14, 11))
    transcript = Transcript(username="tester")
    transcript.append("user", "查看当前设计")
    transcript.append("assistant", "已读取布局。")
    view = QTextBrowser()
    view.resize(720, 420)
    view.show()
    transcript.render(view)
    application.processEvents()
    tables = list(view.document().rootFrame().childFrames())
    assert tables[0].format().borderBrush().color().name().lower() == \
        DARK.accent.lower()
    assert tables[1].format().borderBrush().color().name().lower() == \
        DARK.copilot.lower()
    assert view.font().pointSize() == 14
    code = code_block_html("def read(): pass", "python")
    assert DARK.code_block_background in code and DARK.code_keyword in code


def test_terminal_color_schemes_share_the_theme_surface_and_text():
    def rgb(value):
        return ",".join(
            str(int(value[index:index + 2], 16)) for index in (1, 3, 5))

    schemes = Path(__file__).resolve().parents[4] / "share/qtermwidget5/color-schemes"
    for name, palette in (("SiCo", LIGHT), ("SiCoDark", DARK)):
        parser = configparser.ConfigParser()
        parser.read(schemes / (name + ".colorscheme"))
        assert parser["Background"]["Color"] == rgb(palette.background)
        assert parser["Foreground"]["Color"] == rgb(palette.text)


def test_workspace_snapshot_accepts_appearance_and_rejects_unknown_theme(tmp_path):
    path = tmp_path / "nested" / "workspace.ini"
    settings = open_settings(path)
    settings.setValue("workspace/geometry", QByteArray(b"geometry"))
    settings.setValue("workspace/state", QByteArray(b"state"))
    save_appearance(settings, AppearancePreferences("dark", 15, 9))
    raw = path.read_bytes()
    validate_stream(io.BytesIO(raw))
    settings.setValue(THEME_KEY, "sepia")
    settings.sync()
    with pytest.raises(ValueError):
        validate_stream(io.BytesIO(path.read_bytes()))


def test_dark_default_and_legacy_font_sizes_migrate_once(tmp_path):
    assert AppearancePreferences().theme == "dark"

    path = tmp_path / "workspace.ini"
    settings = QSettings(str(path), QSettings.IniFormat)
    settings.setValue("appearance/text_pt", 9)
    settings.setValue("appearance/label_pt", 9)
    settings.sync()

    assert load_appearance(settings) == AppearancePreferences("dark", 10, 10)

    save_appearance(settings, AppearancePreferences("light", 9, 9))
    assert load_appearance(settings) == AppearancePreferences("light", 9, 9)
