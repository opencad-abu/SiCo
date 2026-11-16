"""Migration lock descriptors obey NFS access rules without mutating legacy data."""

import errno
import fcntl
import os

import pytest

from sico.migration.filesystem import Tree


@pytest.mark.parametrize("exclusive", [False, True])
def test_nfs_lock_access_is_limited_to_exclusive_lock_files(tmp_path, monkeypatch, exclusive):
    legacy = tmp_path / ".cad"
    service = legacy / "ai/agent/service"
    service.mkdir(parents=True, mode=0o700)
    for parent in (legacy, legacy / "ai", legacy / "ai/agent"):
        parent.chmod(0o700)
    lock = service / "owner.lock"
    lock.touch(mode=0o600)
    data = legacy / "ai/agent-provider.json"
    data.write_text('{"provider":"fixture"}\n')
    data.chmod(0o600)
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (lock, data)}
    original = fcntl.flock
    observed = []

    def nfs_flock(fd, operation):
        access = fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE
        observed.append((operation, access))
        if operation & fcntl.LOCK_EX and access == os.O_RDONLY:
            raise OSError(errno.EBADF, "NFS exclusive locks require write access")
        return original(fd, operation)

    monkeypatch.setattr(fcntl, "flock", nfs_flock)
    tree = Tree(legacy, exclusive=exclusive)
    try:
        _rows, blockers = tree.inventory()
        assert not blockers
        assert observed == [(fcntl.LOCK_NB | (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH),
                             os.O_RDWR if exclusive else os.O_RDONLY)]
        with tree.open("ai/agent-provider.json") as stream:
            assert fcntl.fcntl(stream.fileno(), fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY
        with lock.open("r+b") as competitor, pytest.raises(BlockingIOError):
            original(competitor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        tree.close()
    with lock.open("r+b") as competitor:
        original(competitor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert before == {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (lock, data)}
