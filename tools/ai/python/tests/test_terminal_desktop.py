"""Opt-in xcb desktop acceptance using the real terminal binding."""

import os

import pytest
from terminal_smoke_support import run_real_terminal_smoke


@pytest.mark.skipif(
    os.environ.get("CAD_TERMINAL_DESKTOP_PROBE") != "1",
    reason="set CAD_TERMINAL_DESKTOP_PROBE=1 with an X11 display",
)
def test_real_terminal_xcb_desktop():
    run_real_terminal_smoke("xcb")
