"""Journal recovery under interrupted writes and concurrent cache publication."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
import subprocess
import sys
import threading

import pytest

from sico.core.contracts import BoundContext, RunState
from sico.storage import index_cache, journal_records
from sico.storage.history_reader import SessionReader
from sico.storage.index import JournalIndex
from sico.storage.journal import SessionJournal


def state():
    return RunState(BoundContext("instance", "generation", "target", {}))


def test_concurrent_cache_publication_uses_independent_complete_files(tmp_path, monkeypatch):
    with SessionJournal(tmp_path, "concurrent") as journal:
        journal.append("probe.one", {}, state())
        root, directory = journal.root, journal.directory
    cache = directory / "events.idx"
    cache.unlink()
    original = (directory / "events.jsonl").read_bytes()
    barrier, temporary = threading.Barrier(2), []
    replace = os.replace

    def simultaneous(source, target):
        if str(target) == str(cache):
            temporary.append(source)
            barrier.wait(timeout=5)
        return replace(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(index_cache.os, "replace", simultaneous)
        with ThreadPoolExecutor(max_workers=2) as workers:
            jobs = [workers.submit(SessionReader(root, "concurrent").journal_index)
                    for _ in range(2)]
            assert [job.result(10).committed for job in jobs] == [1, 1]
    assert len(set(temporary)) == 2
    assert not list(directory.glob("events.idx.*.tmp"))
    with SessionJournal(tmp_path, "concurrent") as journal:
        loaded, exact = JournalIndex.load(directory, journal._index.fingerprint)
        assert exact and loaded.committed == 1
        assert (directory / "events.jsonl").read_bytes() == original


def test_reader_snapshot_does_not_publish_a_racing_append(tmp_path, monkeypatch):
    with SessionJournal(tmp_path, "growing") as journal:
        journal.append("probe.one", {}, state())
        path = journal.directory / "events.idx"
        path.unlink()
        scan = JournalIndex.scan

        def append_during_scan(index, stream, session_id, *, end):
            journal.append("probe.two", {}, state())
            return scan(index, stream, session_id, end=end)

        with monkeypatch.context() as patch:
            patch.setattr(JournalIndex, "scan", append_during_scan)
            snapshot = SessionReader(journal.root, "growing").journal_index()
        assert snapshot.committed == 1
        assert not path.exists()
        assert SessionReader(journal.root, "growing").journal_index().committed == 2
        with pytest.raises(RuntimeError, match="active writer"):
            SessionJournal(tmp_path, "growing")


@pytest.mark.parametrize("phase", ["create", "replace"])
def test_cache_write_failure_does_not_block_log_or_leave_temporary_files(
        tmp_path, monkeypatch, phase):
    with SessionJournal(tmp_path, "uncached") as journal:
        journal.append("probe.one", {}, state())
        directory = journal.directory
    (directory / "events.idx").unlink()

    def unavailable(*args, **kwargs):
        raise OSError("injected cache write failure")

    with monkeypatch.context() as patch:
        if phase == "create":
            patch.setattr(index_cache.tempfile, "mkstemp", unavailable)
        else:
            patch.setattr(index_cache.os, "replace", unavailable)
        with SessionJournal(tmp_path, "uncached") as journal:
            assert len(journal.events()) == 1
            journal.append("probe.two", {}, state())
    assert not list(directory.glob("events.idx.*.tmp"))
    with SessionJournal(tmp_path, "uncached") as journal:
        assert [row["sequence"] for row in journal.events()] == [1, 2]


@pytest.mark.parametrize("failure", ["partial_write", "fsync"])
def test_interrupted_append_keeps_every_complete_record(tmp_path, monkeypatch, failure):
    journal = SessionJournal(tmp_path, "interrupted")
    journal.append("probe.one", {}, state())
    path, fd = journal.directory / "events.jsonl", journal._fd
    original, write, fsync = path.read_bytes(), os.write, os.fsync
    calls = []

    def interrupted_write(target, data):
        if target == fd:
            if calls:
                raise OSError("injected partial write")
            calls.append(True)
            return write(target, data[:19])
        return write(target, data)

    def interrupted_sync(target):
        if target == fd:
            raise OSError("injected fsync failure")
        return fsync(target)

    with monkeypatch.context() as patch:
        patch.setattr(os, "write" if failure == "partial_write" else "fsync",
                      interrupted_write if failure == "partial_write" else interrupted_sync)
        with pytest.raises(OSError, match="injected"):
            journal.append("probe.two", {}, state())
    assert journal._fd == journal._lock == -1
    failed = path.read_bytes()
    assert failed.startswith(original)
    with SessionJournal(tmp_path, "interrupted") as recovered:
        expected = original if failure == "partial_write" else failed
        assert path.read_bytes() == expected
        recovered.append("probe.next", {}, state())
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    assert len(rows) == (2 if failure == "partial_write" else 3)
    assert [row["sequence"] for row in rows] == list(range(1, len(rows) + 1))


def test_process_exit_releases_writer_and_recovers_only_its_torn_tail(tmp_path):
    code = """
import os, sys
from sico.storage.journal import SessionJournal
from sico.core.contracts import BoundContext, RunState
journal = SessionJournal(sys.argv[1], 'crashed')
journal.append('probe.one', {}, RunState(BoundContext('i', 'g', 't', {})))
os.write(journal._fd, b'{"unfinished":')
os._exit(17)
"""
    result = subprocess.run([sys.executable, "-s", "-c", code, str(tmp_path)],
                            capture_output=True, timeout=10)
    assert result.returncode == 17, result.stderr.decode()
    path = tmp_path / ".sico/ai/agent/sessions/crashed/events.jsonl"
    complete = path.read_bytes().splitlines(keepends=True)[0]
    with SessionJournal(tmp_path, "crashed") as recovered:
        assert path.read_bytes() == complete
        recovered.append("probe.two", {}, state())
        assert len(recovered.events()) == 2


def test_changed_log_during_recovery_is_preserved(tmp_path, monkeypatch):
    with SessionJournal(tmp_path, "changed") as journal:
        journal.append("probe.one", {}, state())
        path = journal.directory / "events.jsonl"
    path.write_bytes(path.read_bytes() + b'{"unfinished":')
    scan = JournalIndex.scan

    def change_after_scan(index, stream, session_id, *, end):
        result = scan(index, stream, session_id, end=end)
        with path.open("ab") as target:
            target.write(b'"raced"}')
        return result

    original = path.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(JournalIndex, "scan", change_after_scan)
        with pytest.raises(ValueError, match="变化"):
            SessionJournal(tmp_path, "changed")
    assert path.read_bytes() == original + b'"raced"}'


def test_warm_open_and_read_do_not_parse_the_historical_prefix(tmp_path, monkeypatch):
    with SessionJournal(tmp_path, "warm") as journal:
        for _ in range(300):
            journal.append("model.delta", {"text": "x" * 8192}, state())
        root = journal.root
    calls, decode = [], journal_records.strict_json

    def counted(raw):
        calls.append(len(raw))
        return decode(raw)

    monkeypatch.setattr(journal_records, "strict_json", counted)
    with SessionJournal(tmp_path, "warm") as journal:
        assert len(journal.records) == 300
        assert SessionReader(root, "warm").journal_index().committed == 300
        assert len(calls) == 1
