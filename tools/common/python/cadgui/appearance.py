"""Appearance preferences shared by SiCo and the standalone family GUIs."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from PyQt5.QtCore import QSettings
from PyQt5.QtGui import QColor, QFont, QPalette
from PyQt5.QtWidgets import QApplication

from .fonts import apply_ui_family, install as install_fonts, ui_family
from .palette import activate as activate_palette
from .theme import window_stylesheet

THEME_LIGHT = "light"
THEME_DARK = "dark"
DEFAULT_THEME = THEME_DARK
THEMES = {THEME_DARK: "黑暗", THEME_LIGHT: "明亮"}

THEME_KEY = "appearance/theme"
TEXT_POINT_KEY = "appearance/text_pt"
LABEL_POINT_KEY = "appearance/label_pt"
APPEARANCE_VERSION_KEY = "appearance/version"
KEYS = frozenset({THEME_KEY, TEXT_POINT_KEY, LABEL_POINT_KEY, APPEARANCE_VERSION_KEY})

TEXT_MIN, TEXT_MAX = 8, 24
LABEL_MIN, LABEL_MAX = 8, 20
DEFAULT_POINT = 10
APPEARANCE_VERSION = 2
LEGACY_DEFAULT_POINT = 9


@dataclass(frozen=True)
class AppearancePreferences:
    """One complete visual preference set; absent values use the system size."""

    theme: str = DEFAULT_THEME
    text_point_size: int = DEFAULT_POINT
    label_point_size: int = DEFAULT_POINT

    def validated(self):
        if self.theme not in THEMES:
            raise ValueError("未知主题：" + str(self.theme))
        if not _point(self.text_point_size, TEXT_MIN, TEXT_MAX):
            raise ValueError("正文文字大小必须在 %d–%d pt。" % (TEXT_MIN, TEXT_MAX))
        if not _point(self.label_point_size, LABEL_MIN, LABEL_MAX):
            raise ValueError("界面标签大小必须在 %d–%d pt。" % (LABEL_MIN, LABEL_MAX))
        return self


def _point(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def system_point_size(application=None):
    """The desktop font size used until the user records a preference."""
    application = QApplication.instance() if application is None else application
    point = application.font().pointSize() if application is not None else 0
    return max(DEFAULT_POINT, point) if point > 0 else DEFAULT_POINT


def load_appearance(settings, *, default_point_size=None):
    """Read the appearance keys, falling back independently when absent."""
    default_point_size = (system_point_size() if default_point_size is None
                          else default_point_size)
    theme = settings.value(THEME_KEY, DEFAULT_THEME)
    if theme not in THEMES:
        theme = DEFAULT_THEME
    version = _stored_version(settings)
    text_point_size = _value(settings, TEXT_POINT_KEY, default_point_size,
                             TEXT_MIN, TEXT_MAX)
    label_point_size = _value(settings, LABEL_POINT_KEY, default_point_size,
                              LABEL_MIN, LABEL_MAX)
    if version < APPEARANCE_VERSION:
        # 9 pt was the pre-Sarasa system default; treat it as unset once so
        # existing projects inherit the new 10 pt default.
        if text_point_size == LEGACY_DEFAULT_POINT:
            text_point_size = DEFAULT_POINT
        if label_point_size == LEGACY_DEFAULT_POINT:
            label_point_size = DEFAULT_POINT
    return AppearancePreferences(
        theme=theme,
        text_point_size=text_point_size,
        label_point_size=label_point_size,
    )


def _stored_version(settings):
    try:
        return int(settings.value(APPEARANCE_VERSION_KEY, 1))
    except (TypeError, ValueError):
        return 1


def _value(settings, key, fallback, minimum, maximum):
    if not settings.contains(key):
        return fallback
    try:
        value = int(settings.value(key))
    except (TypeError, ValueError):
        return fallback
    return value if minimum <= value <= maximum else fallback


def save_appearance(settings, preferences):
    """Persist one validated preference set and flush it to disk."""
    preferences.validated()
    path = Path(settings.fileName())
    if not path.parent.exists():
        path.parent.mkdir(mode=0o700, parents=True)
        os.chmod(path.parent, 0o700)
    settings.setValue(THEME_KEY, preferences.theme)
    settings.setValue(TEXT_POINT_KEY, preferences.text_point_size)
    settings.setValue(LABEL_POINT_KEY, preferences.label_point_size)
    settings.setValue(APPEARANCE_VERSION_KEY, APPEARANCE_VERSION)
    settings.sync()
    if settings.status() != QSettings.NoError:
        raise OSError("外观设置未能写入工作区配置文件。")
    if os.path.isfile(path):
        os.chmod(path, 0o600)


def validate_stored_appearance(settings):
    """Reject malformed appearance keys in a migration snapshot."""
    if settings.contains(APPEARANCE_VERSION_KEY):
        try:
            version = int(settings.value(APPEARANCE_VERSION_KEY))
        except (TypeError, ValueError):
            raise ValueError("Unsupported appearance version") from None
        if version < 1:
            raise ValueError("Unsupported appearance version")
    if settings.contains(THEME_KEY):
        theme = settings.value(THEME_KEY)
        if not isinstance(theme, str) or theme not in THEMES:
            raise ValueError("Unsupported appearance theme")
    for key, minimum, maximum in (
        (TEXT_POINT_KEY, TEXT_MIN, TEXT_MAX),
        (LABEL_POINT_KEY, LABEL_MIN, LABEL_MAX),
    ):
        if not settings.contains(key):
            continue
        try:
            value = int(settings.value(key))
        except (TypeError, ValueError):
            raise ValueError("Unsupported appearance font size") from None
        if not minimum <= value <= maximum:
            raise ValueError("Unsupported appearance font size")


def saved_appearance_path():
    """The workspace appearance file for the current project, when available."""
    try:
        from sicostate import root

        return root() / "ai/agent/workspace.ini"
    except (OSError, ValueError):
        return None


def load_saved_appearance(application=None):
    """Read the current project's preferences; missing storage means dark."""
    application = QApplication.instance() if application is None else application
    path = saved_appearance_path()
    if path is None:
        return AppearancePreferences(
            text_point_size=system_point_size(application),
            label_point_size=system_point_size(application),
        )
    settings = QSettings(str(path), QSettings.IniFormat)
    settings.setFallbacksEnabled(False)
    try:
        return load_appearance(
            settings, default_point_size=system_point_size(application))
    finally:
        del settings


def apply_application_appearance(application, preferences):
    """Apply palette, interface font, native palette and stylesheet."""
    install_fonts(application)
    palette = activate_palette(preferences.theme)
    font = QFont(application.font())
    apply_ui_family(font)
    font.setPointSize(preferences.label_point_size)
    application.setFont(font)
    application.setPalette(application_palette(palette, application.palette()))
    application.setStyleSheet(application_stylesheet(preferences))


def apply_saved_appearance(application=None):
    """Load and apply the project appearance for a standalone family GUI."""
    application = QApplication.instance() if application is None else application
    if application is None:
        return AppearancePreferences()
    preferences = load_saved_appearance(application)
    apply_application_appearance(application, preferences)
    return preferences


def application_palette(palette, base=None):
    """Qt palette roles for native controls not covered by stylesheet selectors."""
    result = QPalette(base) if base is not None else QPalette()
    for role, colour in (
        (QPalette.Window, palette.background),
        (QPalette.WindowText, palette.text),
        (QPalette.Base, palette.background),
        (QPalette.AlternateBase, palette.selection),
        (QPalette.Text, palette.text),
        (QPalette.Link, palette.link),
        (QPalette.LinkVisited, palette.link),
        (QPalette.Button, palette.background),
        (QPalette.ButtonText, palette.text),
        (QPalette.BrightText, palette.on_accent),
        (QPalette.Highlight, palette.accent),
        (QPalette.HighlightedText, palette.on_accent),
        (QPalette.ToolTipBase, palette.background),
        (QPalette.ToolTipText, palette.text),
    ):
        result.setColor(role, QColor(colour))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        result.setColor(QPalette.Disabled, role, QColor(palette.muted))
    role = getattr(QPalette, "PlaceholderText", None)
    if role is not None:
        result.setColor(role, QColor(palette.muted))
    return result


def application_stylesheet(preferences):
    """Family colours plus the independent document-text size."""
    return window_stylesheet() + (
        "\nQTextBrowser, QTextEdit, QPlainTextEdit {\n"
        "    font-family: %s;\n"
        "    font-size: %dpt;\n"
        "}\n" % (ui_family(), preferences.text_point_size)
    )


__all__ = (
    "APPEARANCE_VERSION", "APPEARANCE_VERSION_KEY", "AppearancePreferences",
    "DEFAULT_POINT", "DEFAULT_THEME", "KEYS", "LABEL_MAX", "LABEL_MIN", "LABEL_POINT_KEY",
    "LEGACY_DEFAULT_POINT", "TEXT_MAX", "TEXT_MIN", "TEXT_POINT_KEY",
    "THEME_DARK", "THEME_KEY", "THEME_LIGHT", "THEMES",
    "apply_application_appearance", "apply_saved_appearance", "application_palette",
    "application_stylesheet", "load_appearance", "load_saved_appearance",
    "save_appearance", "saved_appearance_path", "system_point_size",
    "validate_stored_appearance",
)
