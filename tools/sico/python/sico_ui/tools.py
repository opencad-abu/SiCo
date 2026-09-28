"""SiCo workspace tools: small utilities opened from the 工具 menu."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QMenu, QWidget

from .glyphs import ACTION_GLYPH_SIZE, terminal_icon
from .terminal_tool import open_terminal


@dataclass(frozen=True)
class ToolEntry:
    """One entry of the workspace 工具 menu."""

    key: str
    title: str
    glyph: Callable[[int], QIcon]
    open: Callable[[QWidget], None]


TOOLS: tuple[ToolEntry, ...] = (
    ToolEntry("terminal", "终端", terminal_icon, open_terminal),
)


def install_tools_menu(menu: QMenu, window: QWidget) -> None:
    """Populate the 工具 menu; every tool opens its own window."""

    for tool in TOOLS:
        action = menu.addAction(tool.glyph(ACTION_GLYPH_SIZE), tool.title)
        action.setObjectName(f"tool_{tool.key}")
        action.triggered.connect(
            lambda _checked=False, entry=tool: entry.open(window)
        )
