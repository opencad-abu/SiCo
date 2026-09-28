"""Run-directory reservations shared by Virtuoso, queued jobs, and the CLI."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket


class RunBusy(RuntimeError):
    pass


@contextmanager
def _guard(run_dir: Path):
    directory = run_dir.expanduser().resolve()
    root = directory.parent / ".cad-rce-locks"
    root.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(str(directory).encode()).hexdigest()
    owner = root / f"{key}.json"
    # The guard inode is permanent: unlinking it would allow concurrent locks.
    with (root / f"{key}.lock").open("a+") as guard:
        try:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RunBusy(f"RCE run directory is in use: {directory}") from exc
        yield directory, owner, guard.fileno()


def _read_owner(owner: Path) -> dict:
    return json.loads(owner.read_text()) if owner.exists() else {}


def _verify(directory: Path, owner: Path, token: str) -> None:
    data = _read_owner(owner)
    if not token or data.get("token") != token:
        raise RunBusy(
            f"RCE run directory is reserved: {directory}; reservation: {owner}"
        )


def reserve(run_dir: Path, token: str) -> None:
    if not token:
        raise ValueError("An RCE reservation token is required")
    with _guard(run_dir) as (directory, owner, _):
        if owner.exists():
            raise RunBusy(
                f"RCE run directory is reserved: {directory}; reservation: {owner}"
            )
        owner.write_text(
            json.dumps(
                {
                    "run_dir": str(directory),
                    "token": token,
                    "host": socket.gethostname(),
                    "pid": os.getpid(),
                }
            )
            + "\n"
        )


def check(run_dir: Path, token: str) -> None:
    with _guard(run_dir) as (directory, owner, _):
        _verify(directory, owner, token)


def release(run_dir: Path, token: str) -> None:
    with _guard(run_dir) as (directory, owner, _):
        if owner.exists():
            _verify(directory, owner, token)
            owner.unlink()


@contextmanager
def execution_lock(run_dir: Path, token: str | None = None):
    with _guard(run_dir) as (directory, owner, descriptor):
        if token:
            _verify(directory, owner, token)
        elif owner.exists():
            raise RunBusy(
                f"RCE run directory is reserved: {directory}; reservation: {owner}"
            )
        # The runner passes this descriptor to active extraction stages, so an
        # orphaned tool can still block a new run after its parent exits.
        yield descriptor
