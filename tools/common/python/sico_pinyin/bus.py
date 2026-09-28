"""Private IBus/libpinyin setup inside an owned D-Bus process tree."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from sicoprocess import run


def supervise(control, directory):
    """Owner EOF (including SIGKILL) reaps all IBus/D-Bus descendants."""
    import sys

    directory = Path(directory)
    # D-Bus activation (notably dconf-service) must inherit the same private
    # directories as its clients, before dbus-run-session starts the bus.
    os.environ.update(XDG_CONFIG_HOME=str(directory / "config"),
                      XDG_CACHE_HOME=str(directory / "cache"),
                      XDG_RUNTIME_DIR=str(directory / "runtime"))
    command = ["dbus-run-session", "--", sys.executable, "-s", "-c",
               "import sys; from sico_pinyin.bus import serve; serve(sys.argv[1])",
               str(directory)]
    try:
        return run(control, command, forward_status=True)
    finally:
        # This process itself holds dbus.log as stdout/stderr. NFS keeps an open
        # unlinked log as .nfs*, preventing rmdir after an abrupt GUI death.
        for fd in (1, 2):
            os.close(fd)
        shutil.rmtree(directory)


def serve(directory):
    directory = Path(directory)
    env = dict(os.environ)
    for name in ("IBUS_ADDRESS", "IBUS_USE_PORTAL", "XMODIFIERS"):
        env.pop(name, None)
    env.update(GSETTINGS_BACKEND="dconf", XDG_CONFIG_HOME=str(directory / "config"),
               XDG_CACHE_HOME=str(directory / "cache"),
               XDG_RUNTIME_DIR=str(directory / "runtime"),
               IBUS_ADDRESS_FILE=str(directory / "ibus.address"),
               GIO_USE_VFS="local", NO_AT_BRIDGE="1")
    for key, value in (("preload-engines", "['libpinyin']"),
                       ("engines-order", "['libpinyin']"), ("use-global-engine", "true")):
        subprocess.run(["gsettings", "set", "org.freedesktop.ibus.general", key, value],
                       env=env, check=True, timeout=5)
    daemon = subprocess.Popen(["ibus-daemon", "--address=unix:tmpdir=/tmp", "--cache=none"],
                              env=env, start_new_session=True)
    try:
        address = directory / "ibus.address"
        deadline = time.monotonic() + 8
        while not (address.exists() and address.stat().st_size):
            if daemon.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("Private IBus did not start")
            time.sleep(.05)
        client = dict(env)
        client.pop("DBUS_SESSION_BUS_ADDRESS", None)
        engines = subprocess.run(["ibus", "list-engine"], env=client, timeout=8,
                                 check=True, capture_output=True, text=True).stdout
        if not re.search(r"^\s*libpinyin\s+-", engines, re.MULTILINE):
            raise RuntimeError("libpinyin engine is unavailable")
        deadline = time.monotonic() + 5
        while True:
            # EL7 IBus can return 1 after successfully changing the engine.
            # Read back the actual state before declaring the private bus ready.
            subprocess.run(["ibus", "engine", "libpinyin"], env=client, timeout=3,
                           capture_output=True)
            current = subprocess.run(["ibus", "engine"], env=client, timeout=3,
                                     capture_output=True, text=True)
            if current.returncode == 0 and current.stdout.strip() == "libpinyin":
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Private IBus did not select libpinyin")
            time.sleep(.05)
        (directory / "ready").write_text("ready\n")
        daemon.wait()
    finally:
        if daemon.poll() is None:
            daemon.terminate()
        daemon.wait(timeout=3)
