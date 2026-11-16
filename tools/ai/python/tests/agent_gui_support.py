"""Owned Xvfb desktop helpers for opt-in tests; no interaction with the user's display."""

from __future__ import annotations

import os
import select
import subprocess
import time
from contextlib import contextmanager


def wait_until(predicate, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError("Timed out waiting for owned desktop")


@contextmanager
def isolated_display(directory):
    read_fd, write_fd = os.pipe()
    with (directory / "xvfb.log").open("w") as log:
        server = subprocess.Popen(
            [
                "Xvfb",
                "-displayfd",
                str(write_fd),
                "-screen",
                "0",
                "1400x1000x24",
                "-nolisten",
                "tcp",
            ],
            pass_fds=(write_fd,),
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        os.close(write_fd)
        try:
            assert select.select([read_fd], [], [], 10)[0], "Xvfb did not start"
            number = os.read(read_fd, 32).decode().strip()
            env = {
                k: v
                for k, v in os.environ.items()
                if not k.startswith(("QT_", "SICO_"))
                and k
                not in {
                    "LD_LIBRARY_PATH",
                    "LD_PRELOAD",
                    "PYTHONPATH",
                    "PYTHONHOME",
                    "SICO_PROVIDER_CONFIG",
                    "SICO_ROOT",
                }
            }
            env.update(
                DISPLAY=":" + number,
                CAD_PYTHON="/software/pkgs/python/3.9.13/bin/python3",
                PWD=str(directory),
            )
            yield env
        finally:
            os.close(read_fd)
            server.terminate()
            server.wait(timeout=5)


def xdo(env, *arguments, check=True):
    return subprocess.run(
        ["xdotool", *arguments], env=env, capture_output=True, text=True, timeout=5, check=check
    ).stdout.strip()


def screenshot(env, path):
    code = (
        "from PyQt5.QtWidgets import QApplication; import sys; "
        "a=QApplication([]); a.primaryScreen().grabWindow(0).save(sys.argv[1])"
    )
    subprocess.run(
        [env["CAD_PYTHON"], "-s", "-c", code, str(path)],
        env=env,
        check=True,
        capture_output=True,
        timeout=10,
    )


def quick_form(env, text=None, *, cancel=False, submit=True):
    """Actual keyboard/mouse input on the SKILL-launched PyQt composer.

    The composer is frameless now, so the input sits one title bar lower.
    """
    window = wait_until(
        lambda: xdo(env, "search", "--onlyvisible", "--name", "^Ask Silicon Copilot$", check=False)
    ).splitlines()[-1]
    xdo(env, "windowfocus", window)
    if text is not None:
        xdo(env, "mousemove", "--window", window, "160", "120", "click", "1")
        xdo(env, "key", "--clearmodifiers", "ctrl+a")
        for index, line in enumerate(text.split("\n")):
            if index:
                xdo(env, "key", "--clearmodifiers", "shift+Return")
            if line:
                xdo(env, "type", "--clearmodifiers", line)
    if cancel:
        xdo(env, "key", "--clearmodifiers", "Escape")
        wait_until(
            lambda: (
                not xdo(
                    env, "search", "--onlyvisible", "--name", "^Ask Silicon Copilot$", check=False
                )
            )
        )
        # The parent observes child EOF asynchronously before the next Ask.
        time.sleep(0.2)
    elif submit:
        xdo(env, "key", "--clearmodifiers", "Return")
    return window
