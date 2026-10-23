"""Bundled Sarasa UI/Mono selection shared by every SiCo Qt application."""

from __future__ import annotations

from dataclasses import dataclass
import sys
from typing import TYPE_CHECKING

from sicoresources import font as bundled_font

if TYPE_CHECKING:
    from PyQt5.QtWidgets import QApplication


UI_FAMILY = "Sarasa UI SC"
MONO_FAMILY = "Sarasa Mono SC"
UI_FALLBACK_FAMILIES = ("Noto Sans CJK SC", "WenQuanYi Zen Hei")
MONO_FALLBACK_FAMILIES = ("Noto Sans Mono CJK SC", "DejaVu Sans Mono")
UI_POINT_SIZE = 10
MONO_POINT_SIZE = 11

# Only the weights the product actually renders are shipped. SemiBold is the
# real face behind the 600-weight transcript labels; it is never synthesized.
FONT_FILES = (
    "SarasaUiSC-Regular.ttf",
    "SarasaUiSC-SemiBold.ttf",
    "SarasaUiSC-Bold.ttf",
    "SarasaMonoSC-Regular.ttf",
    "SarasaMonoSC-Bold.ttf",
)


@dataclass(frozen=True)
class FontSet:
    """The loaded product faces and the files that could not be loaded."""

    ui_family: str = UI_FAMILY
    mono_family: str = MONO_FAMILY
    ui_point_size: int = UI_POINT_SIZE
    mono_point_size: int = MONO_POINT_SIZE
    loaded_files: tuple[str, ...] = ()
    missing_files: tuple[str, ...] = ()


_CURRENT: FontSet | None = None
_FONT_IDS: list[int] = []


def _font_classes():
    from PyQt5.QtGui import QFont, QFontDatabase, QFontMetrics

    return QFont, QFontDatabase, QFontMetrics


def _matching_family(families, preferred):
    if preferred in families:
        return preferred
    return next((family for family in families if family.startswith(preferred)), preferred)


def _set_families(font, families):
    ordered = tuple(dict.fromkeys(family for family in families if family))
    if hasattr(font, "setFamilies"):
        font.setFamilies(list(ordered))
    else:  # Qt 5.9/5.12 used by some standalone EL7 tools.
        font.setFamily(ordered[0])


def _set_rendering(font):
    QFont, _, _ = _font_classes()
    font.setHintingPreference(QFont.PreferVerticalHinting)
    strategy = QFont.PreferAntialias
    no_subpixel = getattr(QFont, "NoSubpixelAntialias", None)
    if no_subpixel is not None:
        strategy |= no_subpixel
    font.setStyleStrategy(strategy)


def apply_ui_family(font):
    """Apply the bundled UI family and EL7-safe raster settings to one font."""
    QFont, _, _ = _font_classes()
    current = _CURRENT or FontSet()
    _set_families(font, (current.ui_family,) + UI_FALLBACK_FAMILIES)
    font.setStyleHint(QFont.SansSerif)
    font.setFixedPitch(False)
    _set_rendering(font)
    return font


def apply_mono_family(font):
    """Apply the bundled monospace family and EL7-safe raster settings."""
    QFont, _, _ = _font_classes()
    current = _CURRENT or FontSet()
    _set_families(font, (current.mono_family,) + MONO_FALLBACK_FAMILIES)
    font.setStyleHint(QFont.Monospace)
    font.setFixedPitch(True)
    _set_rendering(font)
    return font


def install(application: QApplication | None = None, *, force=False) -> FontSet:
    """Load the bundled fonts once, then apply the UI face to the application."""
    global _CURRENT
    QFont, QFontDatabase, _ = _font_classes()
    if application is None:
        from PyQt5.QtWidgets import QApplication

        application = QApplication.instance()
    if application is None:
        raise RuntimeError("install SiCo fonts after creating QApplication")
    database = QFontDatabase()
    if _CURRENT is not None and not force:
        available = set(database.families())
        if _CURRENT.ui_family in available and _CURRENT.mono_family in available:
            return _CURRENT
        force = True  # A new QApplication loses application-font registrations.
    if force:
        for identifier in _FONT_IDS:
            database.removeApplicationFont(identifier)
        _FONT_IDS.clear()
    families, loaded, missing = [], [], []
    for name in FONT_FILES:
        try:
            path = bundled_font(name)
        except (OSError, ValueError):
            # A standalone GUI may run before a site installation root is
            # configured; its system font fallback remains usable.
            path = None
        if path is None:
            missing.append(name)
            continue
        identifier = database.addApplicationFont(str(path))
        if identifier < 0:
            missing.append(name)
            continue
        loaded.append(name)
        _FONT_IDS.append(identifier)
        for family in database.applicationFontFamilies(identifier):
            if family not in families:
                families.append(family)

    _CURRENT = FontSet(
        ui_family=_matching_family(families, UI_FAMILY),
        mono_family=_matching_family(families, MONO_FAMILY),
        loaded_files=tuple(loaded),
        missing_files=tuple(missing),
    )
    font = QFont(application.font())
    apply_ui_family(font)
    point_size = font.pointSize()
    if point_size <= 0 or point_size < UI_POINT_SIZE:
        font.setPointSize(UI_POINT_SIZE)
    application.setFont(font)
    if missing:
        sys.stderr.write(
            "SiCo fonts unavailable: " + ", ".join(missing)
            + "; using system fallback\n"
        )
    return _CURRENT


def ui_font(point_size=None, weight=None):
    """One UI font; callers use this instead of hard-coding a family."""
    QFont, _, _ = _font_classes()
    current = _CURRENT or FontSet()
    font = QFont()
    apply_ui_family(font)
    font.setPointSize(current.ui_point_size if point_size is None else point_size)
    if weight is not None:
        font.setWeight(weight)
    return font


def mono_font(point_size=None, weight=None):
    """One code/terminal font with a CJK-capable monospace face."""
    QFont, _, _ = _font_classes()
    current = _CURRENT or FontSet()
    font = QFont()
    apply_mono_family(font)
    font.setPointSize(current.mono_point_size if point_size is None else point_size)
    if weight is not None:
        font.setWeight(weight)
    return font


def is_fixed_pitch(font):
    """Check actual advances; Qt's fontconfig backend does not report the flag."""
    _, _, QFontMetrics = _font_classes()
    metrics = QFontMetrics(font)
    measure = getattr(metrics, "horizontalAdvance", None) or metrics.width
    advances = {measure(char) for char in "iWm0A"}
    return len(advances) == 1 and next(iter(advances)) > 0


def terminal_font(point_size=None):
    """The bundled monospace face when fonts are installed in this process."""
    from PyQt5.QtWidgets import QApplication

    if _CURRENT is None or QApplication.instance() is None:
        return None
    font = mono_font(point_size=point_size)
    return font if is_fixed_pitch(font) else None


def ui_family():
    return (_CURRENT or FontSet()).ui_family


def mono_family():
    return (_CURRENT or FontSet()).mono_family


def ui_css_family():
    return ", ".join(f'"{family}"' for family in (ui_family(),) + UI_FALLBACK_FAMILIES) + ", sans-serif"


def mono_css_family():
    return ", ".join(f'"{family}"' for family in (mono_family(),) + MONO_FALLBACK_FAMILIES) + ", monospace"


__all__ = (
    "FONT_FILES", "FontSet", "MONO_FALLBACK_FAMILIES", "MONO_FAMILY",
    "MONO_POINT_SIZE", "UI_FALLBACK_FAMILIES", "UI_FAMILY", "UI_POINT_SIZE",
    "apply_mono_family", "apply_ui_family", "install", "is_fixed_pitch",
    "mono_css_family", "mono_family", "mono_font", "ui_css_family", "ui_family",
    "ui_font", "terminal_font",
)
