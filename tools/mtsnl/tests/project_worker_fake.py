"""Real subprocess mailbox peer; deliberately does not interpret SKILL."""

from __future__ import annotations

from pathlib import Path
import sys


def fake_ocean(root: Path) -> Path:
    executable = root / "ocean"
    executable.write_text(
        f"#!{sys.executable}\n"
        + r"""
import json, os, re, signal, subprocess, sys, time
from pathlib import Path
root = Path.cwd()
def status(path, text):
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(text)
    tmp.replace(path)
status(root/'ready', str(os.getpid()))
while not (root/'shutdown').exists():
    command = root/'command.il'
    if not command.exists():
        time.sleep(.01)
        continue
    paths = re.findall(r'"(?:\\.|[^"\\])*"', command.read_text())
    task, result, cwd = map(lambda x: Path(json.loads(x)), paths)
    command.unlink()
    text = task.read_text()
    if 'FAKE_HANG' in text:
        child = "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(120)"
        helper = subprocess.Popen([sys.executable, '-c', child])
        (cwd/'helper.pid').write_text(str(helper.pid))
        (cwd/'entered').touch()
        time.sleep(120)
    if 'FAKE_CRASH' in text:
        sys.exit(9)
    with (root/'tasks.log').open('a') as log:
        log.write('task-result\n')
    status(result, 'error' if 'FAKE_ERROR' in text else 'ok')
"""
    )
    executable.chmod(0o700)
    return executable
