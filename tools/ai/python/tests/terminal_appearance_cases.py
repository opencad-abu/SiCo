"""Terminal appearance regressions."""

from __future__ import annotations

import pytest
from terminal_fixtures import (
    _FakeFont,
    _FakeFontDatabase,
    _FakeFontInfo,
    frontend,
    terminal_session,
)


def test_terminal_font_keeps_a_valid_system_fixed_font(monkeypatch) -> None:
    monkeypatch.setattr(_FakeFontDatabase, "system_family", "System Mono")

    font = frontend._fixed_pitch_terminal_font(
        _FakeFontDatabase, _FakeFont, _FakeFontInfo, allow_bundled=False)

    assert font.family == "System Mono"
    assert font.style_hint is None


def test_terminal_font_falls_back_to_an_installed_fixed_pitch_family(monkeypatch) -> None:
    monkeypatch.setattr(_FakeFontDatabase, "system_family", "System Sans")

    font = frontend._fixed_pitch_terminal_font(
        _FakeFontDatabase, _FakeFont, _FakeFontInfo, allow_bundled=False)

    assert font.family == "Liberation Mono"
    assert font.style_hint == _FakeFont.Monospace
    assert font.fixed_pitch is True


def test_input_method_cursor_refresh_targets_the_focused_terminal_surface() -> None:
    updates: list[int] = []

    class InputMethod:
        def update(self, query: int) -> None:
            updates.append(query)

    class Application:
        @staticmethod
        def inputMethod() -> InputMethod:
            return InputMethod()

    class Qt:
        ImCursorRectangle = 2

    class Surface:
        def __init__(self, focused: bool) -> None:
            self.focused = focused

        def hasFocus(self) -> bool:
            return self.focused

    class Terminal:
        def __init__(self, surface: Surface) -> None:
            self.surface = surface

        def focusProxy(self) -> Surface:
            return self.surface

    surface = Surface(True)
    terminal = Terminal(surface)

    assert terminal_session._refresh_input_method_cursor(Application, terminal, Qt)
    assert updates == [Qt.ImCursorRectangle]
    surface.focused = False
    assert not terminal_session._refresh_input_method_cursor(Application, terminal, Qt)
    assert updates == [Qt.ImCursorRectangle]


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("  Layout    debug  ", "Layout debug"),
        ("\t\n", ""),
        ("a" * 60, "a" * 48),
    ),
)
def test_session_title_is_compact_single_line(value: str, expected: str) -> None:
    assert frontend._normalize_session_title(value) == expected


def test_scroll_restore_uses_pyqt_single_shot_overload() -> None:
    calls: list[tuple[int, object]] = []

    class Timer:
        @staticmethod
        def singleShot(delay: int, callback) -> None:
            calls.append((delay, callback))

    class ScrollBar:
        def __init__(self) -> None:
            self.value = None

        def setValue(self, value: int) -> None:
            self.value = value

    scroll_bar = ScrollBar()
    frontend._schedule_scroll_restore(Timer, scroll_bar, 37)

    assert len(calls) == 1
    assert calls[0][0] == 0
    calls[0][1]()
    assert scroll_bar.value == 37
