"""Silicon Copilot icons and Qt rendering from the public resource contract."""

from __future__ import annotations

from sicoresources import icon as resource_icon

from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QColor, QIcon, QPainter, QPixmap

from .theme import BACKGROUND

LOGO = "logo-dark.png"
_BRAND = frozenset({"logo.png", "logo-dark.png", "logo-light.png", "logo-clear.png", "ai-small.png"})
_STATUS = frozenset({"active.png", "check.png", "correct.png", "error.png",
                     "idel.png", "info.png", "info2.png", "pend.png"})
_icons = {}


def share_path(name):
    """Resolve one icon name; terminal data has its own independent API."""
    category = "brand" if name in _BRAND else "status" if name in _STATUS else "actions"
    path = resource_icon(category, name)
    return str(path) if path is not None else None


def icon(name, fallback=None):
    """One shared icon; a missing asset degrades to ``fallback`` instead of failing."""
    path = share_path(name)
    if path is None:
        return fallback if fallback is not None else QIcon()
    cached = _icons.get(path)
    if cached is None:
        cached = _icons[path] = QIcon(path)
    return cached


def tinted(pixmap, color):
    """Flat silhouette of one icon; the share folder holds no inverted asset."""
    tint = QPixmap(pixmap.size())
    tint.fill(Qt.transparent)
    painter = QPainter(tint)
    painter.drawPixmap(0, 0, pixmap)
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(tint.rect(), QColor(color))
    painter.end()
    return tint


def filled_icon(value, size=(18, 18), color=BACKGROUND):
    """Icon for one accent-filled button: light glyph while the button is usable.

    Hover keeps the same glyph, and a disabled button falls back to the
    original dark asset because its background is white again.
    """
    base = value.pixmap(QSize(*size))
    light = tinted(base, color)
    icon = QIcon()
    for state in (QIcon.Off, QIcon.On):
        icon.addPixmap(light, QIcon.Normal, state)
        icon.addPixmap(light, QIcon.Active, state)
    icon.addPixmap(base, QIcon.Disabled, QIcon.Off)
    return icon


def logo_path():
    return share_path(LOGO)


def logo_icon():
    return icon(LOGO)


def install_logo(target):
    """Give one top-level window, or the whole application, the shared logo."""
    value = logo_icon()
    if not value.isNull():
        target.setWindowIcon(value)
    return value
