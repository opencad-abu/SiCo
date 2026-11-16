"""Local legacy flock and NFS-server record locks share a single owner."""

import errno
import fcntl
import subprocess
import sys

import pytest

from sicolock import lock


@pytest.mark.parametrize("protocol", ["flock", "lockf"])
def test_owned_lock_excludes_legacy_and_nfs_record_contenders(tmp_path, protocol):
    path = tmp_path / "owner.lock"
    with path.open("w+b") as owner:
        lock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # An unrelated close must not drop the descriptor-owned record lock.
        with path.open("r+b"):
            pass
        script = """
import fcntl,sys
with open(sys.argv[1], 'r+b') as stream:
    try:
        getattr(fcntl,sys.argv[2])(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(7)
"""
        result = subprocess.run([sys.executable, "-s", "-c", script, str(path), protocol],
                                capture_output=True, timeout=5)
        assert result.returncode == 7, result.stderr
    result = subprocess.run([sys.executable, "-s", "-c", script, str(path), protocol],
                            capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr


def test_record_conflict_releases_partial_legacy_lock(tmp_path):
    path = tmp_path / "owner.lock"
    with path.open("w+b") as remote, path.open("r+b") as contender:
        fcntl.lockf(remote, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            lock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with path.open("r+b") as legacy:
            fcntl.flock(legacy, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_shared_readers_and_explicit_unlock(tmp_path):
    path = tmp_path / "owner.lock"
    path.touch()
    with path.open("rb") as first, path.open("rb") as second, path.open("r+b") as writer:
        lock(first, fcntl.LOCK_SH | fcntl.LOCK_NB)
        lock(second, fcntl.LOCK_SH | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            lock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock(first, fcntl.LOCK_UN)
        lock(second, fcntl.LOCK_UN)
        lock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_missing_record_lock_support_never_falls_back(tmp_path, monkeypatch):
    path = tmp_path / "owner.lock"
    original = fcntl.fcntl

    def unavailable(fd, command, argument):
        if command == fcntl.F_OFD_SETLK:
            raise OSError(errno.EOPNOTSUPP, "record locking unavailable")
        return original(fd, command, argument)

    monkeypatch.setattr(fcntl, "fcntl", unavailable)
    with path.open("w+b") as owner:
        with pytest.raises(OSError, match="unavailable"):
            lock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with path.open("r+b") as legacy:
            fcntl.flock(legacy, fcntl.LOCK_EX | fcntl.LOCK_NB)
