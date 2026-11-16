"""Actual shell teardown after the nonblocking owner shutdown sequence."""

import os

from PyQt5.QtTest import QTest


def verify_running_shutdown(application, window):
    # Reopen an independent session in the visible final-tab window.
    window._add_session_from_menu()
    session = window._current_session()
    pid = session.shell_pid()
    assert pid > 0
    window.shutdown_from_owner()
    assert window._closing._shutdown.active
    for _ in range(400):
        application.processEvents()
        if not window._sessions:
            break
        QTest.qWait(10)
    assert not window._sessions
    assert not window.isVisible()
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        application.processEvents()
        QTest.qWait(10)
    raise AssertionError(f"terminal shell {pid} survived window shutdown")
