"""Selectable family palettes shared by SiCo windows.

The dark palette is the product default.  The light palette remains the
historical SiCo look and an explicit user choice.  Both keep the red-brown
accent unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Palette:
    """Every colour a family surface may need, named by responsibility."""

    name: str
    background: str
    stripe: str
    text: str
    selection: str
    copilot: str
    link: str
    muted: str
    error: str
    stop: str
    accent: str
    accent_dark: str
    on_accent: str
    code_keyword: str
    code_string: str
    code_comment: str
    code_number: str
    code_builtin: str
    code_block_background: str
    table_border: str
    input_ready: str
    input_blocked: str
    state_ready: str
    state_busy: str
    state_warn: str
    state_stale: str
    state_error: str
    activity_active: str
    activity_pending: str
    activity_free: str
    activity_idle: str
    status_queued: str
    status_executing: str
    status_waiting: str
    status_completed: str
    status_failed: str


LIGHT = Palette(
    name="light",
    background="#FEFEFE",
    stripe="#F6F5F4",
    text="#000000",
    selection="#BCBCBC",
    copilot="#BCBCBC",
    link="#1565C0",
    muted="#6E6E6E",
    error="#C0392B",
    stop="#F44336",
    accent="#B4543A",
    accent_dark="#96442D",
    on_accent="#FEFEFE",
    code_keyword="#B4543A",
    code_string="#2E7D32",
    code_comment="#6E6E6E",
    code_number="#1565C0",
    code_builtin="#000000",
    code_block_background="#F4F4F4",
    table_border="#888888",
    input_ready="#2E7D32",
    input_blocked="#BCBCBC",
    state_ready="#2E9E4F",
    state_busy="#2D7FF9",
    state_warn="#E08600",
    state_stale="#6E6E6E",
    state_error="#C0392B",
    activity_active="#2E9E4F",
    activity_pending="#E08600",
    activity_free="#2D7FF9",
    activity_idle="#9AA0A6",
    status_queued="#6B7280",
    status_executing="#2563EB",
    status_waiting="#B45309",
    status_completed="#15803D",
    status_failed="#B91C1C",
)


DARK = Palette(
    name="dark",
    background="#1C1917",
    stripe="#25211F",
    text="#F5F1EE",
    selection="#473B36",
    copilot="#9A8982",
    link="#66B2FF",
    muted="#B9ADA7",
    error="#F28B82",
    stop="#FF7369",
    # The product accent is intentionally identical in both themes.
    accent="#B4543A",
    accent_dark="#96442D",
    on_accent="#F5F1EE",
    code_keyword="#B4543A",
    code_string="#A5D6A7",
    code_comment="#A79B95",
    code_number="#64B5F6",
    code_builtin="#F5F1EE",
    code_block_background="#292321",
    table_border="#6F625C",
    input_ready="#81C995",
    input_blocked="#5A4F4A",
    state_ready="#66BB6A",
    state_busy="#64B5F6",
    state_warn="#FFB74D",
    state_stale="#B9ADA7",
    state_error="#F28B82",
    activity_active="#66BB6A",
    activity_pending="#FFB74D",
    activity_free="#64B5F6",
    activity_idle="#A8A29E",
    status_queued="#A8A29E",
    status_executing="#60A5FA",
    status_waiting="#FBBF24",
    status_completed="#4ADE80",
    status_failed="#F87171",
)


PALETTES: Mapping[str, Palette] = {LIGHT.name: LIGHT, DARK.name: DARK}
_ACTIVE = DARK


def palette(name=None):
    """Return a palette by name; an unknown name is a programming error."""
    if name is None:
        return _ACTIVE
    try:
        return PALETTES[name]
    except KeyError:
        raise ValueError("Unknown SiCo palette: " + str(name)) from None


def active():
    """The palette currently used by new Qt surfaces and document rendering."""
    return _ACTIVE


def activate(name):
    """Select the process palette and return it."""
    global _ACTIVE
    _ACTIVE = palette(name)
    return _ACTIVE
