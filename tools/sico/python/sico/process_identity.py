"""Read Linux boot and process identities without launching or signalling processes."""

from dataclasses import dataclass
import os
from pathlib import Path


def process_identity(pid):
    start, _, state, group = _stat(Path('/proc', str(pid), 'stat'))
    return dict(pid=pid, start=str(start), group=group, state=state)


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


@dataclass(frozen=True)
class ProcessObservation:
    """Kernel-observed host identity and directory, never a signal capability."""

    pid: int
    start: int
    parent_pid: int
    cwd: str


def _stat(path):
    fields = path.read_text().rsplit(")", 1)[1].split()
    return int(fields[19]), int(fields[1]), fields[0], int(fields[2])


def _descendants(root, owner):
    pending, seen = [owner], set()
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        if len(seen) > 4096:
            raise ValueError("Process observation exceeds descendant budget")
        try:
            for task in (root / str(pid) / "task").iterdir():
                pending.extend(int(value) for value in (task / "children").read_text().split())
                if len(pending) + len(seen) > 4096:
                    raise ValueError("Process observation exceeds descendant budget")
        except (FileNotFoundError, ProcessLookupError):
            continue
        if pid != owner:
            yield pid


def _owned(root, pid, owner, owner_start):
    """Recheck current ancestry; discovery alone cannot establish membership."""
    seen = set()
    while pid != owner:
        if pid in seen or pid <= 1 or len(seen) >= 4096:
            return False
        seen.add(pid)
        pid = _stat(root / str(pid) / "stat")[1]
    start, _, state, _ = _stat(root / str(owner) / "stat")
    return start == owner_start and state not in ("Z", "X")


def observe_descendant(owner, owner_start, namespace_pid, namespace, executable, *, proc_root=Path('/proc')):
    """Resolve an untrusted namespace hint only inside a live supervised tree.

    Identity, executable, namespace, current ancestry and cwd are read from the
    host kernel. Missing, ambiguous or changing observations fail closed; this
    function never proves termination, sends signals, or authorizes attachment.
    The owner must retain custody while calling it. cwd is a point-in-time fact,
    not proof of a command's start directory without a separate execution barrier.
    """
    if (type(namespace_pid) is not int or not 1 <= namespace_pid <= 2**31 - 1
            or not isinstance(namespace, str) or not namespace.startswith("pid:[")
            or not namespace.endswith("]") or not namespace[5:-1].isdigit()
            or not isinstance(executable, str) or not executable.startswith("/")):
        raise ValueError("Invalid process observation hint")
    root = Path(proc_root)
    expected_executable = os.path.realpath(executable)
    matches = []
    for pid in _descendants(root, owner):
        path = root / str(pid)
        try:
            before = _stat(path / "stat")
            if before[2] in ("Z", "X") or os.readlink(path / "ns/pid") != namespace:
                continue
            fields = dict(line.split(":", 1) for line in (path / "status").read_text().splitlines()
                          if ":" in line)
            if int(fields.get("NSpid", "0").split()[-1]) != namespace_pid:
                continue
            actual_executable = os.readlink(path / "exe")
            if actual_executable != expected_executable or not _owned(root, pid, owner, owner_start):
                continue
            cwd = os.readlink(path / "cwd")
            after = _stat(path / "stat")
            if (not cwd.startswith("/") or cwd.endswith(" (deleted)") or before[:2] != after[:2]
                    or after[2] in ("Z", "X") or before[3] != after[3]
                    or os.readlink(path / "exe") != actual_executable
                    or os.readlink(path / "ns/pid") != namespace
                    or os.readlink(path / "cwd") != cwd):
                raise ValueError("Process identity or directory changed during observation")
            matches.append(ProcessObservation(pid, before[0], before[1], cwd))
        except (FileNotFoundError, ProcessLookupError):
            continue
    if len(matches) != 1 or not _owned(root, matches[0].pid, owner, owner_start):
        raise ValueError("No unique live owned process matches the namespace hint")
    match = matches[0]
    path = root / str(match.pid)
    start, parent, state, _ = _stat(path / "stat")
    if ((start, parent) != (match.start, match.parent_pid) or state in ("Z", "X")
            or os.readlink(path / "ns/pid") != namespace
            or os.readlink(path / "exe") != expected_executable
            or os.readlink(path / "cwd") != match.cwd):
        raise ValueError("Owned process changed before observation completed")
    return matches[0]
