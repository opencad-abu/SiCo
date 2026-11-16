"""Terminal input-method refresh and keyboard scroll policy."""

from __future__ import annotations

import pytest

from cad_ai_terminal.input_behavior import (
    _refresh_input_method_cursor,
    _schedule_scroll_restore,
    apply_scroll_policy,
)


class Qt:
    Key_Up = 1
    Key_Down = 2
    Key_PageUp = 3
    Key_PageDown = 4
    Key_Home = 5
    Key_End = 6
    Key_Backspace = 7
    Key_Delete = 8
    Key_Insert = 9
    Key_Left = 10
    Key_Right = 11
    Key_Return = 12
    Key_Enter = 13
    Key_Tab = 14
    Key_Backtab = 15
    Key_Escape = 16
    ShiftModifier = 0x100
    Vertical = 1
    ImCursorRectangle = 99


class Event:
    def __init__(self, key, *, text="", modifiers=0):
        self._key = key
        self._text = text
        self._modifiers = modifiers

    def key(self):
        return self._key

    def text(self):
        return self._text

    def modifiers(self):
        return self._modifiers


class ScrollBar:
    def __init__(self, value=3, maximum=10):
        self._value = value
        self._maximum = maximum
        self.restored = []

    def value(self):
        return self._value

    def maximum(self):
        return self._maximum

    def setValue(self, value):
        self.restored.append(value)

    def orientation(self):
        return Qt.Vertical


class Terminal:
    def __init__(self, bars=()):
        self.bars = list(bars)
        self.scrolled = 0

    def findChildren(self, _type):
        return self.bars

    def scrollToEnd(self):
        self.scrolled += 1


def test_scroll_restore_uses_two_argument_single_shot_and_ignores_deleted_bar():
    calls = []

    class Timer:
        @staticmethod
        def singleShot(delay, callback):
            calls.append((delay, callback))

    bar = ScrollBar()
    _schedule_scroll_restore(Timer, bar, 3)
    assert calls[0][0] == 0
    calls[0][1]()
    assert bar.restored == [3]

    class Deleted:
        def setValue(self, _value):
            raise RuntimeError("deleted")

    _schedule_scroll_restore(Timer, Deleted(), 1)
    calls[1][1]()


def test_input_method_refresh_handles_missing_unfocused_and_deleted_surface():
    assert _refresh_input_method_cursor(object(), None, Qt) is False

    class Surface:
        def __init__(self, focused=True):
            self.focused = focused

        def hasFocus(self):
            return self.focused

    class Application:
        updates = []

        @classmethod
        def inputMethod(cls):
            return cls

        @classmethod
        def update(cls, value):
            cls.updates.append(value)

    class Proxy:
        def __init__(self, surface):
            self.surface = surface

        def focusProxy(self):
            return self.surface

    assert _refresh_input_method_cursor(Application, Proxy(Surface(False)), Qt) is False
    assert _refresh_input_method_cursor(Application, Proxy(Surface(True)), Qt) is True
    assert Application.updates == [Qt.ImCursorRectangle]


def test_input_method_refresh_does_not_raise_for_a_deleted_terminal():
    class Deleted:
        def focusProxy(self):
            raise RuntimeError("deleted")

    class Application:
        pass

    assert _refresh_input_method_cursor(Application, Deleted(), Qt) is False


def test_scroll_policy_returns_for_shift_navigation_and_repositions_manual_scroll():
    terminal = Terminal([ScrollBar()])
    restored = []
    apply_scroll_policy(
        terminal, None, qt=Qt, scroll_bar_type=ScrollBar, timer_type=object,
        schedule_scroll_restore=lambda *args: restored.append(args),
    )
    apply_scroll_policy(
        terminal, Event(Qt.Key_Up, modifiers=Qt.ShiftModifier), qt=Qt,
        scroll_bar_type=ScrollBar, timer_type=object,
        schedule_scroll_restore=lambda *args: restored.append(args),
    )
    assert restored == []

    apply_scroll_policy(
        terminal, Event(Qt.Key_Up), qt=Qt, scroll_bar_type=ScrollBar,
        timer_type=object, schedule_scroll_restore=lambda *args: restored.append(args),
    )
    assert len(restored) == 1 and restored[0][2] == 3
    assert terminal.scrolled == 0


def test_scroll_policy_scrolls_for_text_and_input_keys_but_not_plain_letters():
    terminal = Terminal()
    apply_scroll_policy(
        terminal, Event(Qt.Key_Up), qt=Qt, scroll_bar_type=ScrollBar,
        timer_type=object, schedule_scroll_restore=lambda *_: None,
    )
    assert terminal.scrolled == 0
    for key in (Qt.Key_Delete, Qt.Key_Return, Qt.Key_Escape):
        apply_scroll_policy(
            terminal, Event(key), qt=Qt, scroll_bar_type=ScrollBar,
            timer_type=object, schedule_scroll_restore=lambda *_: None,
        )
    assert terminal.scrolled == 3
    apply_scroll_policy(
        terminal, Event(99, text="x"), qt=Qt, scroll_bar_type=ScrollBar,
        timer_type=object, schedule_scroll_restore=lambda *_: None,
    )
    assert terminal.scrolled == 4
