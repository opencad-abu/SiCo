"""Catalog scans reuse validated offsets and never block snapshot readers."""

import os
import threading

import pytest

from sico.core.contracts import BoundContext, RunState
from sico.service.catalog import CatalogService
from sico.storage.history import CatalogIndex, SessionReader
from sico.storage.journal import SessionJournal


def test_incremental_catalog_reads_only_new_records_and_keeps_no_transcript(tmp_path, monkeypatch):
    with SessionJournal(tmp_path, "catalog") as journal:
        state = RunState(BoundContext("i", "g", "target", {}))
        journal.append("session.created", {}, state)
        journal.append("codex.thread", {"thread_id": "thread"}, state)
        journal.append("codex.thread.name", {"name": "first"}, state)
        reads = []
        # The catalog only needs envelope checks, so it scans records without
        # payload validation; tracing that path proves reads stay incremental.
        original = SessionReader.iter_raw

        def traced(reader, stream, **options):
            reads.append((stream.tell(), options.get("sequence", 0)))
            yield from original(reader, stream, **options)

        monkeypatch.setattr(SessionReader, "iter_raw", traced)
        index = CatalogIndex(journal.root)
        first = index.scan()
        assert first["catalog"]["name"] == "first"
        assert len(reads) == 1
        assert index.scan() == first
        assert len(reads) == 1
        offset = (journal.directory / "events.jsonl").stat().st_size
        journal.append("model.delta", {"text": "large transcript" * 1000}, state)
        journal.append("codex.thread.name", {"name": "second"}, state)
        assert index.scan()["catalog"]["name"] == "second"
        assert reads[-1] == (offset, 3)
        assert all(event["kind"] != "model.delta"
                   for event in index._entries["catalog"]["metadata"])


def test_catalog_retries_partial_tail_and_invalidates_replaced_or_unsafe_logs(tmp_path):
    with SessionJournal(tmp_path, "catalog") as journal:
        state = RunState(BoundContext("i", "g", "target", {}))
        journal.append("codex.thread.name", {"name": "first"}, state)
        path = journal.directory / "events.jsonl"
        first = path.read_bytes()
        journal.append("codex.thread.name", {"name": "second"}, state)
        complete = path.read_bytes()
        path.write_bytes(complete[:-10])
        index = CatalogIndex(journal.root)
        assert index.scan()["catalog"]["name"] == "first"
        assert path.read_bytes() == complete[:-10]
        with path.open("ab") as stream:
            stream.write(complete[-10:])
        assert index.scan()["catalog"]["name"] == "second"
        path.write_bytes(first)
        assert index.scan()["catalog"]["name"] == "first"
        replacement = path.with_suffix(".replacement")
        replacement.write_bytes(complete)
        replacement.chmod(0o600)
        os.replace(replacement, path)
        assert index.scan()["catalog"]["name"] == "second"
        path.chmod(0o644)
        assert index.scan()["catalog"]["unavailable"]
        path.chmod(0o600)
        path.write_bytes(b"broken\n")
        assert index.scan()["catalog"]["unavailable"]


def test_catalog_snapshot_stays_available_during_slow_scan_and_is_detached(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    owner = threading.get_ident()
    threads = []

    def slow_scan(index, progress=None):
        threads.append(threading.get_ident())
        entered.set()
        assert release.wait(5)
        return {"one": {"id": "one", "modified": 1, "name": "first"}}

    monkeypatch.setattr(CatalogIndex, "scan", slow_scan)
    service = CatalogService(tmp_path, lambda: ())
    try:
        assert entered.wait(5)
        assert service.snapshot()["version"] == 0
        assert service.snapshot(0) is None
        future = service.request()
        assert not future.done()
        release.set()
        future.result(5)
        snapshot = service.snapshot()
        version = snapshot["version"]
        with pytest.raises(TypeError, match="read-only"):
            snapshot["rows"][0]["name"] = "changed by reader"
        assert service.snapshot() is snapshot
        assert service.snapshot()["rows"][0]["name"] == "first"
        service.request().result(5)
        assert service.snapshot(version) is None
        assert threads and all(thread != owner for thread in threads)
    finally:
        release.set()
        service.close()
        assert service.wait(5)


def test_partial_scan_never_turns_unvisited_live_session_into_unavailable(tmp_path, monkeypatch):
    from types import SimpleNamespace
    entered, release = threading.Event(), threading.Event()
    rows = {'live': {'id': 'live', 'modified': 1, 'name': 'live'},
            'newer': {'id': 'newer', 'modified': 2, 'name': 'newer'}}
    controller = SimpleNamespace(session_id='live', closing=False)
    calls = [0]

    def scan(index, progress=None):
        calls[0] += 1
        if calls[0] == 2:
            progress({'newer': rows['newer']})
            entered.set()
            assert release.wait(5)
        return rows if calls[0] <= 2 else {'newer': rows['newer']}

    monkeypatch.setattr(CatalogIndex, 'scan', scan)
    service = CatalogService(tmp_path, lambda: (controller,))
    try:
        pending = service.request()
        assert entered.wait(5)
        partial = service.snapshot()
        assert 'unavailable' not in partial['retained']['live']
        assert {row['id'] for row in partial['rows']} == {'live', 'newer'}
        release.set()
        pending.result(5)
        service.request().result(5)
        assert service.snapshot()['retained']['live']['unavailable'] == '记录不可用'
    finally:
        release.set()
        service.close()
        assert service.wait(5)


def test_initial_partial_scan_omits_unvisited_live_session():
    from types import SimpleNamespace
    result = CatalogService._value({'new': {'id': 'new', 'modified': 2}},
                                   [SimpleNamespace(session_id='live', closing=False)],
                                   complete=False)
    assert not result['retained']
    assert 'live' not in result['availability']


@pytest.mark.parametrize("reason", ["", "已删除", "记录不可用"])
def test_runtime_closure_does_not_change_record_availability(reason):
    from types import SimpleNamespace

    row = dict(id="ended", modified=1)
    if reason:
        row["unavailable"] = reason
    result = CatalogService._value({"ended": row},
                                  [SimpleNamespace(session_id="ended", closing=True)])
    assert result["retained"]["ended"].get("unavailable", "") == reason
    assert result["availability"]["ended"] == reason
    assert bool(result["rows"]) == (not reason)
