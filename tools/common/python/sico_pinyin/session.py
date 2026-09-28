"""Select desktop IME or own one private LSF input-method lifetime."""

from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from sicopaths import installation
from sicoprocess import require_subreaper
from sicotemp import ai_directory

IME_NAMES = ("QT_IM_MODULE", "XMODIFIERS", "DBUS_SESSION_BUS_ADDRESS", "IBUS_ADDRESS",
             "IBUS_ADDRESS_FILE", "IBUS_USE_PORTAL")
_ACTIVE = 0


def lsf_session(environment):
    value = environment.get("LSB_JOBID", "")
    return value.isascii() and value.isdigit() and int(value) > 0


def require_plugin():
    from PyQt5.QtCore import QLibraryInfo

    plugin = Path(QLibraryInfo.location(QLibraryInfo.PluginsPath)) / (
        "platforminputcontexts/libibusplatforminputcontextplugin.so")
    if not plugin.is_file():
        raise ValueError("Qt IBus input-context plugin is unavailable")


class PrivateBus:
    def __init__(self, launch_dir):
        self.child = None
        self.control = None
        self.directory = None
        require_subreaper()
        require_plugin()
        for name in ("ibus", "ibus-daemon", "dbus-daemon", "dbus-run-session", "gsettings"):
            if not shutil.which(name):
                raise ValueError(name + " is unavailable")
        common = str(installation().path("tools/common/python"))
        runtime = ai_directory(cwd=launch_dir, create=True) / "runtime"
        runtime.mkdir(mode=0o700, exist_ok=True)
        from sicostate import validate_directory

        validate_directory(runtime)
        self.directory = Path(tempfile.mkdtemp(prefix="sico-ai-pinyin.", dir=runtime))
        try:
            self._start(common)
        except BaseException:
            self.close()
            raise

    def _start(self, common):
        for name in ("config", "cache", "runtime"):
            (self.directory / name).mkdir(mode=0o700)
        read_fd, self.control = os.pipe()
        env = dict(os.environ)
        for name in IME_NAMES:
            env.pop(name, None)
        env["PYTHONPATH"] = common
        env.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
        try:
            with (self.directory / "dbus.log").open("w") as log:
                self.child = subprocess.Popen(
                    [sys.executable, "-s", "-c",
                     "import sys; from sico_pinyin.bus import supervise; "
                     "raise SystemExit(supervise(int(sys.argv[1]), sys.argv[2]))",
                     str(read_fd), str(self.directory)],
                    env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    pass_fds=(read_fd,), start_new_session=True)
        finally:
            os.close(read_fd)

    def ready(self):
        deadline = time.monotonic() + 20
        while not (self.directory / "ready").exists():
            if self.child.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("Private IBus/libpinyin startup failed")
            time.sleep(.05)
        return str(self.directory / "ibus.address")

    def close(self):
        if self.control is not None:
            os.close(self.control)
            self.control = None
        if self.child is not None:
            self.child.wait(timeout=5)
        if self.directory is not None and self.directory.exists():
            shutil.rmtree(self.directory)


@contextmanager
def input_session(launch_dir=None):
    """Do not replace a workstation's configured IBus/Fcitx/XIM session."""
    global _ACTIVE
    if _ACTIVE or not lsf_session(os.environ):
        yield
        return
    old = {name: os.environ.get(name) for name in IME_NAMES}
    bus = None
    try:
        try:
            bus = PrivateBus(launch_dir)
            address = bus.ready()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            if bus is not None:
                bus.close()
                bus = None
            print("SiCo: pinyin input is unavailable on this LSF host; "
                  f"continuing without it ({exc})", file=sys.stderr)
            address = None
        for name in IME_NAMES:
            os.environ.pop(name, None)
        if address:
            os.environ.update(QT_IM_MODULE="ibus", IBUS_ADDRESS_FILE=address)
        _ACTIVE += 1
        try:
            yield
        finally:
            _ACTIVE -= 1
    finally:
        if bus is not None:
            bus.close()
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
