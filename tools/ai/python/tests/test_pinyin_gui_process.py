"""Real X11/IBus/libpinyin input and owner-death cleanup on a private display."""

import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import time

import pytest

ROOT = Path(os.environ.get("SICO_TEST_INSTALLATION") or Path(__file__).resolve().parents[4])
PYTHON = os.environ.get("SICO_TEST_PYTHON", sys.executable)
pytestmark = pytest.mark.skipif(
    os.environ.get("SICO_IME_TESTS") != "1" or
    not all(shutil.which(n) for n in ("Xvfb", "xdotool", "ibus", "ibus-daemon")) or
    not Path("/usr/share/ibus/component/libpinyin.xml").is_file(),
    reason="Opt-in real X11/IBus/libpinyin acceptance")


def until(predicate, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.1)
    raise AssertionError("Private input-method probe timed out")


@contextmanager
def display(directory):
    read_fd, write_fd = os.pipe()
    with (directory / "xvfb.log").open("w") as log:
        xvfb = subprocess.Popen(["Xvfb", "-displayfd", str(write_fd), "-screen", "0",
                                  "1400x1000x24", "-nolisten", "tcp"],
                                 pass_fds=(write_fd,), stdout=log, stderr=log)
        os.close(write_fd)
        try:
            assert select.select([read_fd], [], [], 10)[0]
            number = os.read(read_fd, 32).decode().strip()
            assert number.isdigit(), "Xvfb failed; see xvfb.log"
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(("CAD_", "SICO_", "QT_", "PYTHON", "LD_", "DBUS_", "IBUS_", "XDG_"))
                   and not any(n in k.upper() for n in ("KEY", "TOKEN", "SECRET", "PASSWORD"))}
            (directory / "home").mkdir()
            env.update(DISPLAY=":" + number, SICO_HOME=str(ROOT), SICO_PYTHON=PYTHON,
                       SICO_PYTHON_ROOT=str(Path(PYTHON).parents[1]),
                       SICO_SYSTEM_LD_LIBRARY_PATH=f'{Path(PYTHON).parents[1]}/lib:{ROOT}/lib',
                       PYTHONPATH=":".join(str(ROOT / f"tools/{tool}/python")
                                           for tool in ("common", "sico", "ai")),
                       PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1",
                       HOME=str(directory / "home"), PWD=str(directory), LANG="en_US.UTF-8",
                       LSB_JOBID="123456", GIO_USE_VFS="local", NO_AT_BRIDGE="1")
            yield env
        finally:
            os.close(read_fd)
            xvfb.terminate()
            xvfb.wait(5)


def xdo(env, *args, check=True):
    return subprocess.run(["xdotool", *args], env=env, capture_output=True, text=True,
                          timeout=5, check=check).stdout.strip()


def type_chinese(env, directory, title, *, focus_shortcut=None):
    window = until(lambda: xdo(env, "search", "--onlyvisible", "--name", title,
                              check=False)).splitlines()[-1]
    xdo(env, "windowfocus", window)
    if focus_shortcut:
        xdo(env, "key", "--clearmodifiers", focus_shortcut)
    else:
        xdo(env, "mousemove", "--window", window, "160", "120", "click", "1")
    address = until(lambda: next(directory.glob(".sico/ai/runtime/sico-ai-pinyin.*/ibus.address"), None))
    client = dict(env, IBUS_ADDRESS_FILE=str(address))
    client.pop("DBUS_SESSION_BUS_ADDRESS", None)

    def engine():
        result = subprocess.run(["ibus", "engine"], env=client, capture_output=True,
                                text=True, timeout=5)
        return result.stdout.strip() == "libpinyin"

    until(engine, 10)
    xdo(env, "type", "--clearmodifiers", "--delay", "100", "zhongwen")
    time.sleep(.3)
    screenshot = ("from PyQt5.QtWidgets import QApplication; import sys; "
                  "a=QApplication([]); a.primaryScreen().grabWindow(0).save(sys.argv[1])")
    subprocess.run([PYTHON, "-s", "-c", screenshot, str(directory / "pinyin-preedit.png")],
                   env=env, check=True, capture_output=True, timeout=10)
    xdo(env, "key", "space")
    xdo(env, "type", "--clearmodifiers", "--delay", "100", "shuru")
    xdo(env, "key", "space")
    subprocess.run([PYTHON, "-s", "-c", screenshot, str(directory / "pinyin-committed.png")],
                   env=env, check=True, capture_output=True, timeout=10)
    return window


def test_real_pinyin_in_sico_quick_composer(tmp_path):
    with display(tmp_path) as env, (tmp_path / "composer.log").open("w") as log:
        process = subprocess.Popen([str(ROOT / "tools/sico/bin/sico"), "quick-input",
                                    "--launch-dir", str(tmp_path)], env=env, cwd=tmp_path,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log)
        try:
            process.stdin.write((json.dumps(dict(kind="open", source="中文输入验收",
                                                 destination="private probe")) + "\n").encode())
            process.stdin.flush()
            type_chinese(env, tmp_path, "^Ask Silicon Copilot$")
            xdo(env, "key", "Return")
            assert select.select([process.stdout], [], [], 12)[0]
            line = process.stdout.readline().decode()
            assert line.startswith("@text "), line
            assert base64.b64decode(line.split()[1]).decode() == "中文输入"
            process.stdin.write(b'{"kind":"accepted"}\n')
            process.stdin.flush()
            assert process.wait(8) == 0
            assert not list(tmp_path.glob(".sico/ai/runtime/sico-ai-pinyin.*"))
        finally:
            if process.poll() is None:
                process.stdin.close()
                try:
                    process.wait(8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(5)
            process.stdout.close()


def test_ime_owner_sigkill_reaps_private_daemons_and_removes_only_own_files(tmp_path):
    code = '''
import json,os,time
from pathlib import Path
from sico_pinyin.session import PrivateBus
from sicoprocess import _children
bus=PrivateBus(Path.cwd());bus.ready()
pending=[bus.child.pid];pids=[]
while pending:
    pid=pending.pop();pids.append(pid);pending.extend(_children(pid))
Path('owned.json').write_text(json.dumps(dict(pids=pids,directory=str(bus.directory))))
time.sleep(60)
'''
    with display(tmp_path) as env, (tmp_path / "owner.log").open("w") as log:
        sibling = subprocess.Popen([PYTHON, "-c", "import time; time.sleep(60)"], env=env)
        process = subprocess.Popen([PYTHON, "-s", "-c", code], env=env, cwd=tmp_path, stderr=log)
        sentinel = tmp_path / "keep.txt"
        sentinel.write_text("keep")
        try:
            until(lambda: (tmp_path / "owned.json").exists())
            owned = json.loads((tmp_path / "owned.json").read_text())
            process.kill()
            process.wait(5)
            until(lambda: not Path(owned["directory"]).exists(), 8)
            until(lambda: all(not Path('/proc', str(pid)).exists() or
                             Path('/proc', str(pid), 'stat').read_text().split(')')[1].split()[0] == 'Z'
                             for pid in owned['pids']), 8)
            assert sentinel.read_text() == "keep" and sibling.poll() is None
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(5)
            sibling.terminate()
            sibling.wait(5)


def test_real_pinyin_in_main_window(tmp_path):
    with display(tmp_path) as env, (tmp_path / "main.log").open("w") as log:
        entry = str(ROOT / "tools/sico/bin/sico")
        process = subprocess.Popen([entry, "demo", "--gui", "--launch-dir", str(tmp_path),
                                    "--session", "pinyin-main"], env=env, cwd=tmp_path,
                                   stdout=log, stderr=log)
        try:
            type_chinese(env, tmp_path, "^Silicon Copilot$", focus_shortcut="ctrl+l")
            xdo(env, "key", "Return")
            events = tmp_path / '.sico/ai/agent/sessions/pinyin-main/events.jsonl'

            def submitted():
                if not events.exists():
                    return False
                rows = [json.loads(line) for line in events.read_text().splitlines()]
                return any(row['kind'] == 'task.started' and
                           row['payload']['text'] == '中文输入' for row in rows)

            until(submitted)
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(8)
            subprocess.run([entry, "service-stop", "--project-dir", str(tmp_path)],
                           env=env, capture_output=True, timeout=15)
            until(lambda: not list(tmp_path.glob('.sico/ai/runtime/sico-ai-pinyin.*')), 8)


def test_real_pinyin_in_native_terminal(tmp_path):
    entry = ROOT / 'tools/ai/bin/sico-ai-terminal'
    if not entry.is_file() or entry.read_bytes()[:4] != b'\x7fELF':
        pytest.skip('Requires the explicit native terminal candidate')
    with display(tmp_path) as env, (tmp_path / 'terminal.log').open('w') as log:
        read_fd, write_fd = os.pipe()
        env['SICO_AI_CONTROL_FD'] = str(read_fd)
        process = subprocess.Popen([str(entry), '--working-directory', str(tmp_path), '--',
            '/bin/sh', '-c', 'IFS= read -r line; printf "%s" "$line" > terminal-input.txt; sleep 60'],
            env=env, cwd=tmp_path, stdout=log, stderr=log, pass_fds=(read_fd,))
        os.close(read_fd)
        try:
            type_chinese(env, tmp_path, '::AI Assistant@')
            xdo(env, 'key', 'Return')
            until(lambda: (tmp_path / 'terminal-input.txt').exists())
            assert (tmp_path / 'terminal-input.txt').read_text() == '中文输入'
            os.write(write_fd, b'shutdown\n')
            assert process.wait(10) == 0
            until(lambda: not list(tmp_path.glob('.sico/ai/runtime/sico-ai-pinyin.*')), 8)
        finally:
            os.close(write_fd)
            if process.poll() is None:
                process.terminate()
                process.wait(8)
