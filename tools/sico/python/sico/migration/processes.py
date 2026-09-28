"""Observe local process references without reading credentials or issuing signals."""

import os
from pathlib import Path


def beneath(value, roots):
    return any(value == root or value.startswith(root + "/") for root in roots)


def scan_local(source):
    roots = {str(source), os.path.realpath(source)}
    writers, unreadable = set(), 0
    proc = Path("/proc")
    try:
        processes = list(proc.iterdir())
    except OSError:
        return {"scope": "local", "referencing_pids": [], "unreadable_processes": 1}
    for process in processes:
        if not process.name.isdecimal() or int(process.name) == os.getpid():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            for pointer in (process / "cwd", process / "root", *(process / "fd").iterdir()):
                try:
                    target = os.readlink(pointer)
                except FileNotFoundError:
                    continue
                if beneath(target.removesuffix(" (deleted)"), roots):
                    writers.add(int(process.name))
                    break
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError:
            unreadable += 1
    return {"scope": "local", "referencing_pids": sorted(writers),
            "unreadable_processes": unreadable}
