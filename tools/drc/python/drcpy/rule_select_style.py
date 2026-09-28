"""Selector branding resources and application appearance."""

from __future__ import annotations

import os
from pathlib import Path
from sicoresources import icon as resource_icon
from cadgui.chrome import apply_family_style
from .rule_select_qt import QApplication


def logo_path() -> Path | None:
    return resource_icon("brand", "logo.png")


def apply_application_style(application: QApplication) -> None:
    """家族外观：亮底 + 红棕强调，和 SiCo 及其它流程一致。"""

    application.setStyle("Fusion")
    apply_family_style(application)
