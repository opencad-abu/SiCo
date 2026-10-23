"""SiCo 桌面配色：家族主题在 cadgui，这里保持历史导入路径并暴露当前主题。"""

from __future__ import annotations

from cadgui.palette import DARK as DARK_PALETTE
from cadgui.palette import LIGHT as LIGHT_PALETTE
from cadgui.palette import Palette as Palette
from cadgui.palette import activate as activate_palette
from cadgui.palette import active as active_palette
from cadgui.palette import palette as palette
from cadgui.theme import ACCENT as ACCENT
from cadgui.theme import ACCENT_DARK as ACCENT_DARK
from cadgui.theme import BACKGROUND as BACKGROUND
from cadgui.theme import CHROME_STYLESHEET as CHROME_STYLESHEET
from cadgui.theme import CODE_BLOCK_BACKGROUND as CODE_BLOCK_BACKGROUND
from cadgui.theme import CODE_BUILTIN as CODE_BUILTIN
from cadgui.theme import CODE_COMMENT as CODE_COMMENT
from cadgui.theme import CODE_KEYWORD as CODE_KEYWORD
from cadgui.theme import CODE_NUMBER as CODE_NUMBER
from cadgui.theme import CODE_STRING as CODE_STRING
from cadgui.theme import COPILOT as COPILOT
from cadgui.theme import COPILOT_STYLESHEET as COPILOT_STYLESHEET
from cadgui.theme import ERROR as ERROR
from cadgui.theme import INPUT_BLOCKED as INPUT_BLOCKED
from cadgui.theme import INPUT_READY as INPUT_READY
from cadgui.theme import MUTED as MUTED
from cadgui.theme import SELECTION as SELECTION
from cadgui.theme import STOP as STOP
from cadgui.theme import TABLE_BORDER as TABLE_BORDER
from cadgui.theme import TEXT as TEXT
from cadgui.theme import WINDOW_STYLESHEET as WINDOW_STYLESHEET
from cadgui.theme import chrome_stylesheet as chrome_stylesheet
from cadgui.theme import copilot_stylesheet as copilot_stylesheet
from cadgui.theme import window_stylesheet as window_stylesheet

__all__ = (
    "DARK_PALETTE", "LIGHT_PALETTE", "Palette", "activate_palette",
    "active_palette", "palette", "ACCENT", "ACCENT_DARK", "BACKGROUND",
    "CHROME_STYLESHEET", "CODE_BLOCK_BACKGROUND", "CODE_BUILTIN", "CODE_COMMENT",
    "CODE_KEYWORD", "CODE_NUMBER", "CODE_STRING", "COPILOT", "COPILOT_STYLESHEET",
    "ERROR", "INPUT_BLOCKED", "INPUT_READY", "MUTED", "SELECTION", "STOP",
    "TABLE_BORDER", "TEXT", "WINDOW_STYLESHEET", "chrome_stylesheet",
    "copilot_stylesheet", "window_stylesheet",
)
