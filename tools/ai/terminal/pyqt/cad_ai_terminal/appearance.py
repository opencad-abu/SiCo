"""Terminal branding, compact titles and fixed-pitch font selection."""

from __future__ import annotations

import os


MAXIMUM_SESSION_TITLE_LENGTH = 48


def _logo_text() -> str:
    """Return the configured display logo; mirrors the SKILL SICO_logo order."""
    for name in ("LOGO", "COMPANY"):
        value = os.environ.get(name, "").strip()
        if value and value.upper() != "OCAD":
            return value
    return "SiCo"


def _normalize_session_title(value: str) -> str:
    """Return a compact single-line title suitable for a terminal tab."""
    return " ".join(value.split())[:MAXIMUM_SESSION_TITLE_LENGTH].rstrip()


def _fixed_pitch_terminal_font(font_database_type, font_type, font_info_type):
    """Return a font that Qt resolves to fixed pitch on the current host."""
    system_font = font_database_type.systemFont(font_database_type.FixedFont)
    if font_info_type(system_font).fixedPitch():
        return system_font

    database = font_database_type()
    families = database.families()
    preferred = (
        "Monospace",
        "DejaVu Sans Mono",
        "Liberation Mono",
        "Nimbus Mono PS",
        "Courier New",
    )
    family = next(
        (
            candidate
            for candidate in preferred
            if candidate in families and database.isFixedPitch(candidate)
        ),
        next(
            (candidate for candidate in families if database.isFixedPitch(candidate)),
            "Monospace",
        ),
    )
    font = font_type(family)
    font.setStyleHint(font_type.Monospace)
    font.setFixedPitch(True)
    return font
