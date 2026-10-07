"""Passive, session-scoped observations of processes launched by SiCo.

The launcher still owns wait/termination and output pipes. Observations never
reap a child, signal a process, or read from another consumer's pipe.
"""

from __future__ import annotations

import os
import shlex
import stat
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

OUTPUT_LIMIT = 65536
PROCESS_LIMIT = 512
_scope = ContextVar("sico_process_session", default="")
_lock = threading.RLock()
_records = OrderedDict()


def current_session():
    return _scope.get()


@contextmanager
def process_scope(session_id):
    token = _scope.set(session_id or "")
    try:
        yield
    finally:
        _scope.reset(token)


def identity(pid):
    fields = Path("/proc", str(pid), "stat").read_text().rsplit(")", 1)[1].split()
    return {"pid": pid, "start": fields[19], "ppid": int(fields[1]),
            "os_state": fields[0], "rss": max(0, int(fields[21])) * os.sysconf("SC_PAGE_SIZE")}


def observe(expected):
    try:
        value = identity(expected["pid"])
        if value["start"] != expected["start"]:
            return None
        root = Path("/proc", str(value["pid"]))
        value["name"] = (root / "comm").read_text().strip()
        with (root / "cmdline").open("rb") as stream:
            value["argv"] = [s.decode("utf-8", "replace") for s in
                             stream.read(65536).split(b"\0") if s]
        try:
            value["cwd"] = os.readlink(root / "cwd")
        except OSError:
            value["cwd"] = ""
        # Do not combine command data from a replacement PID with this identity.
        if identity(value["pid"])["start"] != expected["start"]:
            return None
        return value
    except (OSError, ValueError, IndexError):
        return None


def track(process, command, cwd, *, name="", logs=(), session_id=None, hidden=False):
    """Register a Popen or a launcher's verified PID; unscoped callers are ignored."""
    session_id = current_session() if session_id is None else session_id
    if not session_id:
        return None
    pid = process if type(process) is int else process.pid
    if type(pid) is not int or pid <= 1:
        return None
    try:
        observed = identity(pid)
    except (OSError, ValueError, IndexError):
        observed = {"pid": pid, "start": ""}
    argv = list(command)
    key = "process_" + uuid.uuid4().hex
    row = {"id": key, "session_id": session_id, "name": name or Path(argv[0]).name,
           "command": shlex.join(argv), "argv": argv, "cwd": str(cwd),
           "started_at": time.time(), "finished_at": None, "exit_code": None,
           "identity": observed, "hidden": hidden, "children": {},
           "logs": tuple(str(p) for p in logs), "output": "", "output_truncated": False,
           "process": None if type(process) is int else process}
    with _lock:
        _records[key] = row
        # Bound retained terminal history; never evict a still-owned launch.
        ended = [k for k, r in _records.items() if r["finished_at"] or r.get("observed_exit_at")]
        for old in ended[:max(0, len(_records) - PROCESS_LIMIT)]:
            _records.pop(old, None)
    return key


def output(key, stdout="", stderr=""):
    """A launcher's cumulative captured output, bounded independently of its pipes."""
    if key is None:
        return
    def decode(value):
        return value.decode("utf-8", "replace") if isinstance(value, bytes) else (value or "")
    value = decode(stdout)
    if stderr:
        value += "\n[stderr]\n" + decode(stderr)
    with _lock:
        if key in _records:
            _records[key].update(output=value[-OUTPUT_LIMIT:],
                                 output_truncated=len(value) > OUTPUT_LIMIT)


def _log_tail(paths):
    parts, limited = [], False
    remaining = OUTPUT_LIMIT
    for path in paths:
        try:
            with open(path, "rb") as stream:
                size = stream.seek(0, os.SEEK_END)
                length = min(size, remaining)
                stream.seek(max(0, size - length))
                raw = stream.read(length)
            parts.append("[" + Path(path).name + "]\n" + raw.decode("utf-8", "replace"))
            limited = limited or size > length
            remaining -= length
        except OSError:
            continue
    text = "\n".join(parts)
    return text[-OUTPUT_LIMIT:], limited or len(text) > OUTPUT_LIMIT


def finish(key, exit_code=None):
    if key is None:
        return
    with _lock:
        row = _records.get(key)
        if row is None or row["finished_at"] is not None:
            return
        paths = row["logs"]
    text, limited = _log_tail(paths)
    with _lock:
        if text:
            row.update(output=text, output_truncated=limited)
        row.update(finished_at=time.time(), exit_code=exit_code, process=None)


def _state(expected, actual, ended):
    if actual is not None:
        return "exited" if actual["os_state"] in {"Z", "X"} else "running"
    if ended:
        return "exited"
    if not expected.get("start"):
        return "unknown"
    try:
        current = identity(expected["pid"])
        if current["start"] != expected["start"] or current["os_state"] in {"Z", "X"}:
            return "exited"
    except FileNotFoundError:
        return "exited"
    except (OSError, ValueError, IndexError):
        pass
    return "unknown"


def _redirected_logs(expected):
    """Read only regular stdout/stderr files, never pipes, PTYs or socket fds."""
    paths, files = [], set()
    if observe(expected) is None:
        return ()
    for number in (1, 2):
        descriptor = Path("/proc", str(expected["pid"]), "fd", str(number))
        try:
            info = descriptor.stat()
            path = os.readlink(descriptor)
            target = os.stat(path)
            token = (info.st_dev, info.st_ino)
            if (stat.S_ISREG(info.st_mode) and token == (target.st_dev, target.st_ino)
                    and token not in files):
                paths.append(path)
                files.add(token)
        except OSError:
            continue
    return tuple(paths) if observe(expected) is not None else ()


def _descendants(root, limit=128):
    """Follow only an observed launch tree, including children of worker threads."""
    found, pending = {}, [root]
    while pending and len(found) < limit:
        parent = pending.pop()
        if observe(parent) is None:
            continue
        for task in Path("/proc", str(parent["pid"]), "task").glob("*/children"):
            try:
                pids = [int(value) for value in task.read_text().split()]
            except (OSError, ValueError):
                continue
            for pid in pids:
                if pid in found or len(found) >= limit:
                    continue
                try:
                    child = observe(identity(pid))
                except (OSError, ValueError, IndexError):
                    continue
                if child and child["ppid"] == parent["pid"]:
                    found[pid] = child
                    pending.append(child)
    return tuple(found.values())


def snapshot(session_id, *, with_output=False, output_id=None):
    """Called by a data worker. Return owned copies; all /proc and log I/O is here."""
    with _lock:
        records = [dict(row, children=dict(row["children"])) for row in _records.values()
                   if row["session_id"] == session_id]
    result, now = [], time.time()
    for record in records:
        expected = record["identity"]
        actual = observe(expected) if expected["start"] else None
        # Keep observing known descendants after their launcher exits/reparents.
        children = dict(record["children"])
        roots = [expected] if actual else list(children.values())
        for root in roots:
            for child in _descendants(root):
                token = (child["pid"], child["start"])
                children.setdefault(token, {**child, "started_at": now})
        with _lock:
            if record["id"] in _records:
                stored = _records[record["id"]]["children"]
                for token, child in children.items():
                    stored.setdefault(token, child)
                expired = [token for token, child in stored.items() if child.get("observed_exit_at")]
                for token in expired[:max(0, len(stored) - PROCESS_LIMIT)]:
                    stored.pop(token, None)
                children = dict(stored)
        observed = [(record["id"], expected, actual, record["started_at"], True)]
        observed.extend((record["id"] + "_" + str(pid) + "_" + start, child,
                         observe(child), child["started_at"], False)
                        for (pid, start), child in children.items())
        for key, previous, live, started, leader in observed:
            if leader and record["hidden"]:
                continue
            code = record["exit_code"] if leader else None
            process = record["process"]
            if leader and code is None and process is not None:
                code = process.returncode  # The owning launcher alone reaps it.
            state = _state(previous, live, leader and (code is not None or record["finished_at"]))
            value = live or previous
            row = {"id": key, "pid": expected["pid"] if leader else previous["pid"],
                   "start": previous.get("start", ""), "ppid": value.get("ppid"),
                   "name": record["name"] if leader else value.get("name", "子进程"),
                   "command": record["command"] if leader else shlex.join(value.get("argv", [])),
                   "argv": record["argv"] if leader else value.get("argv", []),
                   "cwd": record["cwd"] if leader else value.get("cwd", ""),
                   "started_at": started, "finished_at": record["finished_at"] if leader else None,
                   "exit_code": code, "state": state, "os_state": value.get("os_state", ""),
                   "rss": value.get("rss") if live else None,
                   "source": "codex_child" if record["hidden"] else "launch",
                   "parent_id": "" if leader else record["id"], "output": "",
                   "output_truncated": False, "logs": record["logs"] if leader else ()}
            if state == "exited" and row["finished_at"] is None:
                with _lock:
                    original = _records.get(record["id"])
                    if original is not None:
                        owner = original if leader else original["children"].get(
                            (previous["pid"], previous["start"]), {})
                        owner.setdefault("observed_exit_at", now)
                        row["finished_at"] = owner["observed_exit_at"]
            if with_output and leader and output_id in {None, key}:
                text, limited = _log_tail(record["logs"])
                row.update(output=text or record["output"],
                           output_truncated=limited if text else record["output_truncated"])
            elif with_output and not leader and output_id in {None, key}:
                paths = _redirected_logs(previous)
                text, limited = _log_tail(paths)
                row.update(output=text, output_truncated=limited, logs=paths)
            result.append(row)
    return result
