"""Writer-only recovery: the original journal alone authorizes tail removal."""

import os

from .index import JournalIndex
from .journal_records import fingerprint


def writable_index(fd, directory, session_id):
    """Load a complete journal, or strictly scan it before repairing a torn tail.

    The caller holds the exclusive writer lock. No sidecar offset participates
    in the decision to truncate. Complete bad records leave *all* bytes intact.
    """
    before = fingerprint(fd)
    size = before[2]
    incomplete = size > 0 and os.pread(fd, 1, size - 1) != b"\n"
    with os.fdopen(os.dup(fd), "rb") as stream:
        if incomplete:
            index = JournalIndex(before)
            index.scan(stream, session_id, end=size)
        else:
            index = JournalIndex.read(directory, stream, session_id)
    if fingerprint(fd) != before:
        raise ValueError("会话日志在恢复校验期间发生变化")
    if incomplete:
        # scan() validates every complete record before we reach this mutation.
        os.ftruncate(fd, index.covered)
        os.fsync(fd)
        index.fingerprint = fingerprint(fd)
        index.save(directory)
    elif index.covered != size:
        raise ValueError("会话日志未被完整读取")
    return index
