"""Terminal input-method cursor updates and keyboard scroll policy."""

from __future__ import annotations


INPUT_METHOD_CURSOR_SYNC_MS = 50


def _schedule_scroll_restore(timer_type, scroll_bar, position: int) -> None:
    """Restore a terminal scrollbar after QTermWidget handles a key event.

    PyQt5 does not provide the C++ ``singleShot(msec, receiver, callable)``
    overload, so the callback must use the two-argument form.  The runtime
    guard handles a terminal being destroyed before the queued callback runs.
    ``timer_type`` is injected to keep this small compatibility shim testable
    without importing Qt at module load time.
    """

    def restore_scroll_position() -> None:
        try:
            scroll_bar.setValue(position)
        except RuntimeError:
            # The underlying QObject may have been deleted while the callback
            # was pending.
            pass

    timer_type.singleShot(0, restore_scroll_position)


def _refresh_input_method_cursor(application_type, terminal, qt) -> bool:
    """Ask the active Qt input context to re-query the terminal cursor.

    QTermWidget updates its screen asynchronously after ``receivedData``.
    Qt's IBus plugin otherwise keeps the cursor rectangle captured when the
    widget first gained focus, which leaves the candidate popup at (0, 0) for
    full-screen TUIs such as Claude. The caller deliberately invokes this
    after QTermWidget's bounded output-coalescing delay.
    """
    if terminal is None:
        return False
    try:
        surface = terminal.focusProxy() or terminal
        if not surface.hasFocus():
            return False
        application_type.inputMethod().update(qt.ImCursorRectangle)
    except RuntimeError:
        return False
    return True


def apply_scroll_policy(
    terminal, event, *, qt, scroll_bar_type, timer_type, schedule_scroll_restore
) -> None:
    if terminal is None or event is None:
        return
    modifiers = event.modifiers()
    navigation = event.key() in (
        qt.Key_Up,
        qt.Key_Down,
        qt.Key_PageUp,
        qt.Key_PageDown,
        qt.Key_Home,
        qt.Key_End,
    )
    if modifiers & qt.ShiftModifier and navigation:
        return
    if not modifiers & qt.ShiftModifier and event.key() in (
        qt.Key_Up,
        qt.Key_Down,
    ):
        scroll_bar = next(
            (
                candidate
                for candidate in terminal.findChildren(scroll_bar_type)
                if candidate.orientation() == qt.Vertical
            ),
            None,
        )
        if scroll_bar is not None and scroll_bar.value() != scroll_bar.maximum():
            schedule_scroll_restore(timer_type, scroll_bar, scroll_bar.value())
            return
    input_keys = (
        qt.Key_Backspace,
        qt.Key_Delete,
        qt.Key_Insert,
        qt.Key_Left,
        qt.Key_Right,
        qt.Key_Home,
        qt.Key_End,
        qt.Key_Return,
        qt.Key_Enter,
        qt.Key_Tab,
        qt.Key_Backtab,
        qt.Key_Escape,
    )
    if event.text() or event.key() in input_keys:
        terminal.scrollToEnd()
