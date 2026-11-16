# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""router project processes for the opt-in source acceptance."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from router_project_files import _read_fields


def _wait(path, process, timeout, evidence, what):
    def detail():
        text = ""
        for name in ("stdout.log", "CDS.log"):
            source = evidence / name
            if source.exists():
                text += f"\n--- {name} ---\n" + source.read_text()[-3000:]
        return text

    deadline = time.monotonic() + timeout
    while not path.exists():
        assert process.poll() is None, f"{what}: Virtuoso exited early" + detail()
        assert time.monotonic() < deadline, f"{what}: timed out" + detail()
        time.sleep(0.1)
    return _read_fields(path)

def _scan_processes(needle):
    """Return live process rows whose command line contains every marker."""
    rows = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode()
            stat = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            environ = (entry / "environ").read_bytes()
        except OSError:
            continue
        if all(marker in cmdline for marker in needle):
            values = {}
            for item in environ.split(b"\0"):
                key, _, value = item.decode(errors="replace").partition("=")
                if key in {"CAD_NATIVE_ENTRY_PATH", "CAD_RUNTIME_TOOLS", "CAD_NATIVE_ENTRY"}:
                    values[key] = value
            rows.append({"pid": int(entry.name), "ppid": int(stat[1]),
                         "cmdline": cmdline.strip(), "env": values})
    return rows

def _ancestry(pid):
    chain = []
    seen = set()
    while pid > 1 and pid not in seen:
        seen.add(pid)
        chain.append(pid)
        try:
            stat = Path("/proc", str(pid), "stat").read_text().rsplit(")", 1)[1].split()
        except OSError:
            break
        pid = int(stat[1])
    return chain

def _progress_log():
    records = []
    lock = threading.Lock()

    def progress(row):
        with lock:
            records.append(dict(row))

    def rows(session_id, state):
        with lock:
            return [row for row in records
                    if row.get("session_id") == session_id and row.get("state") == state]

    return progress, rows
