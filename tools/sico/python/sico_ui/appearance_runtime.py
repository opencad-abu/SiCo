"""Apply one appearance preference set to the running Qt desktop."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtWidgets import QApplication

from cadgui.appearance import apply_application_appearance

from .appearance import load_appearance, system_point_size
from .branding import logo_icon
from .chrome import refresh_family_style
from .workspace_settings import open_settings

_CURRENT = None


def apply_appearance(preferences, application=None, *, force=False):
    """Activate the palette, fonts and stylesheet, then refresh open windows."""
    global _CURRENT
    application = QApplication.instance() if application is None else application
    if application is None:
        return preferences
    if (not force and _CURRENT == preferences
            and application.font().pointSize() == preferences.label_point_size):
        return preferences
    apply_application_appearance(application, preferences)
    application.setWindowIcon(logo_icon())
    for widget in application.topLevelWidgets():
        refresh_family_style(widget)
        refresh = getattr(widget, "refresh_appearance", None)
        if callable(refresh):
            refresh(preferences)
    _CURRENT = preferences
    return preferences


def apply_saved_appearance(settings_path, *, application=None):
    """Apply the preferences stored beside one project's workspace layout."""
    settings = open_settings(Path(settings_path))
    preferences = load_appearance(
        settings, default_point_size=system_point_size(application))
    return apply_appearance(preferences, application)


def apply_project_appearance(project, *, application=None):
    """Apply a project's saved appearance before its first SiCo window opens."""
    from sicostate import project_directory

    path = project_directory(project, "ai/agent") / "workspace.ini"
    return apply_saved_appearance(path, application=application)
