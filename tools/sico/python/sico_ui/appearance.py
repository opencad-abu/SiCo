"""SiCo appearance settings: shared schema lives in the family cadgui layer."""

from __future__ import annotations

from cadgui.appearance import DEFAULT_POINT as DEFAULT_POINT
from cadgui.appearance import DEFAULT_THEME as DEFAULT_THEME
from cadgui.appearance import KEYS as KEYS
from cadgui.appearance import LABEL_MAX as LABEL_MAX
from cadgui.appearance import LABEL_MIN as LABEL_MIN
from cadgui.appearance import LABEL_POINT_KEY as LABEL_POINT_KEY
from cadgui.appearance import TEXT_MAX as TEXT_MAX
from cadgui.appearance import TEXT_MIN as TEXT_MIN
from cadgui.appearance import TEXT_POINT_KEY as TEXT_POINT_KEY
from cadgui.appearance import THEME_DARK as THEME_DARK
from cadgui.appearance import THEME_KEY as THEME_KEY
from cadgui.appearance import THEME_LIGHT as THEME_LIGHT
from cadgui.appearance import THEMES as THEMES
from cadgui.appearance import AppearancePreferences as AppearancePreferences
from cadgui.appearance import load_appearance as load_appearance
from cadgui.appearance import save_appearance as save_appearance
from cadgui.appearance import system_point_size as system_point_size
from cadgui.appearance import validate_stored_appearance as validate_stored_appearance

__all__ = (
    "AppearancePreferences", "DEFAULT_POINT", "DEFAULT_THEME", "KEYS", "LABEL_MAX",
    "LABEL_MIN", "LABEL_POINT_KEY", "TEXT_MAX", "TEXT_MIN", "TEXT_POINT_KEY", "THEME_DARK",
    "THEME_KEY", "THEME_LIGHT", "THEMES", "load_appearance", "save_appearance",
    "system_point_size", "validate_stored_appearance",
)
