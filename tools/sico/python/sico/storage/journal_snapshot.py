"""Read-only journal snapshots bounded by the owning writer or its released lock."""

from contextlib import contextmanager
import fcntl
import os

from sicolock import lock as state_lock

from .history_reader import owned_directory, validate_event
from .journal import SessionJournal, open_private
from .journal_records import iter_raw_records
from .record_removal import assert_present


def _open(source):
    assert_present(source.root, source.session_id)
    for path in (source.root, source.root / 'sessions', source.directory):
        owned_directory(path)
    return open_private(source.directory / 'events.jsonl', os.O_RDONLY)


@contextmanager
def _snapshot(fd, session_id, end):
    with os.fdopen(fd, 'rb') as stream:
        def events():
            stream.seek(0)
            for row, _, _ in iter_raw_records(stream, session_id, checker=validate_event, end=end):
                yield SessionJournal.public(row)
        yield events


@contextmanager
def reader_snapshot(reader):
    """Released-writer recovery rules accept complete lines, never an unfinished tail.

    Shared ownership prevents a writer or deletion from starting during the read.
    No lock file is created, and no cache, journal or attachment is changed.
    """
    owned_directory(reader.directory)
    owner = open_private(reader.directory / 'writer.lock', os.O_RDONLY)
    try:
        try:
            state_lock(owner, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Active session history requires its owning writer snapshot') from None
        fd = _open(reader)
        with _snapshot(fd, reader.session_id, os.fstat(fd).st_size) as events:
            yield events
    finally:
        os.close(owner)


@contextmanager
def writer_snapshot(journal):
    """Capture only the index boundary published after a successful fsync.

    Capture under the existing append lock, then read on an independent fd so
    later appends need not wait for output pagination. A failed writer cannot
    publish a live snapshot; explicit reopening uses the normal recovery policy.
    """
    with journal._events_lock:
        if journal._fd < 0:
            raise RuntimeError('Journal writer is closed; reopen history explicitly')
        fd = _open(journal)
        end = journal._index.covered
    with _snapshot(fd, journal.session_id, end) as events:
        yield events
