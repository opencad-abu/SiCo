"""Direct-painted glyphs for the LSF monitor."""

from __future__ import annotations

from PyQt5.QtGui import QColor, QFont, QFontDatabase

from cadgui.palette import active

KILL_GLYPH = "\u274c"
REFRESH_GLYPH = "\u27f3"
RELOAD_GLYPH = "\u27f2"
_COLOR_EMOJI_FAMILIES = (
    "Noto Color Emoji",
    "Apple Color Emoji",
    "Segoe UI Emoji",
)
_KILL_GLYPH_PIXELS = 18
_KILL_GLYPH_FALLBACK_PIXELS = 20


def kill_glyph_color() -> QColor:
    """The stop colour from the active family palette."""
    return QColor(active().stop)


def kill_glyph_font() -> QFont:
    families = set(QFontDatabase().families())
    color_family = next(
        (family for family in _COLOR_EMOJI_FAMILIES if family in families), None
    )
    font = QFont(color_family) if color_family is not None else QFont()
    font.setPixelSize(
        _KILL_GLYPH_PIXELS if color_family is not None
        else _KILL_GLYPH_FALLBACK_PIXELS
    )
    return font
