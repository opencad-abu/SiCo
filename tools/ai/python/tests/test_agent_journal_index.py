"""An expendable sidecar must never change or hide durable journal facts."""

import json

import pytest

from sico.core.contracts import BoundContext, RunState
from sico.storage.history_reader import SessionReader
from sico.storage.journal import SessionJournal


def state():
    return RunState(BoundContext("instance", "generation", "target", {}))


@pytest.fixture
def recorded(tmp_path):
    with SessionJournal(tmp_path, "indexed") as journal:
        journal.append("probe.one", {}, state())
        journal.append("codex.thread.name", {"name": "preserved"}, state())
        root, directory = journal.root, journal.directory
    # Reopen once so the cache describes both committed rows.
    with SessionJournal(tmp_path, "indexed"):
        pass
    return root, directory


def corrupt_cache(path, fault):
    lines = path.read_text().splitlines(keepends=True)
    header = lines[0].split()
    rows = [line.split() for line in lines[1:3]]
    trailer = lines[3:]
    if fault == "short_length":
        rows[-1][2] = str(int(rows[-1][2]) - 1)
    elif fault == "short_count":
        header[6], rows = "1", rows[:1]
    elif fault == "empty_entries":
        header[6], rows = "0", []
    elif fault == "sequence":
        rows[-1][0] = "1"
    elif fault == "offset":
        rows[-1][1] = "0"
    elif fault == "negative_length":
        rows[-1][2] = "-1"
    elif fault == "flags":
        rows[-1][3] = "0"
    elif fault == "unknown_flags":
        rows[-1][3] = "256"
    elif fault == "fingerprint":
        header[3] = str(int(header[3]) + 1)
    elif fault == "trailing_garbage":
        trailer.append("garbage\n")
    elif fault == "empty_file":
        path.write_bytes(b"")
        return
    elif fault == "invalid_encoding":
        path.write_bytes(b"\xff\n")
        return
    path.write_text(" ".join(header) + "\n"
                    + "".join(" ".join(row) + "\n" for row in rows)
                    + "".join(trailer))


@pytest.mark.parametrize("consumer", ["writer", "reader"])
@pytest.mark.parametrize("fault", [
    "short_length", "short_count", "empty_entries", "sequence", "offset",
    "negative_length", "flags", "unknown_flags", "fingerprint",
    "trailing_garbage", "empty_file", "invalid_encoding",
])
def test_cache_damage_preserves_all_facts(tmp_path, recorded, consumer, fault):
    root, directory = recorded
    path = directory / "events.jsonl"
    original = path.read_bytes()
    corrupt_cache(directory / "events.idx", fault)
    if consumer == "writer":
        with SessionJournal(tmp_path, "indexed") as journal:
            assert path.read_bytes() == original
            assert [row["kind"] for row in journal.events()] == [
                "probe.one", "codex.thread.name"]
            assert journal.name_metadata["name"] == "preserved"
            journal.append("probe.three", {}, state())
        assert path.read_bytes().startswith(original)
        assert [json.loads(line)["sequence"] for line in path.read_bytes().splitlines()] == [1, 2, 3]
    else:
        reader = SessionReader(root, "indexed")
        index = reader.journal_index()
        assert index is not None and index.committed == 2
        from sico.storage.index import FLAG_METADATA, read_records

        rows = read_records(path, index.select(FLAG_METADATA))
        assert [row["payload"]["name"] for row in rows] == ["preserved"]
        assert reader.tail_window(2).committed == 2
        assert path.read_bytes() == original


@pytest.mark.parametrize("fault", ["syntax", "duplicate", "state", "sequence", "nonfinite"])
@pytest.mark.parametrize("partial_tail", [False, True])
@pytest.mark.parametrize("cached", [True, False])
def test_complete_corruption_never_permits_repair(tmp_path, recorded, fault, partial_tail, cached):
    root, directory = recorded
    path = directory / "events.jsonl"
    rows = path.read_bytes().splitlines(keepends=True)
    row = json.loads(rows[0])
    if fault == "syntax":
        rows[0] = b'{"broken":}\n'
    elif fault == "duplicate":
        rows[0] = b'{"kind":"ignored",' + rows[0][1:]
    else:
        if fault == "state":
            row["state"]["messages"] = "invalid"
        elif fault == "sequence":
            row["sequence"] = True
        else:
            row["payload"]["number"] = float("nan")
        rows[0] = (json.dumps(row) + "\n").encode()
    original = b"".join(rows) + (b'{"unfinished":' if partial_tail else b"")
    path.write_bytes(original)
    if not cached:
        (directory / "events.idx").unlink()
    with pytest.raises(ValueError):
        SessionJournal(tmp_path, "indexed")
    reader = SessionReader(root, "indexed")
    with pytest.raises(ValueError):
        reader.journal_index()
    with pytest.raises(ValueError):
        reader.tail_window(1)
    assert path.read_bytes() == original


@pytest.mark.parametrize("cached", [True, False])
def test_only_unterminated_tail_is_recovered(tmp_path, recorded, cached):
    root, directory = recorded
    path = directory / "events.jsonl"
    original = path.read_bytes()
    if not cached:
        (directory / "events.idx").unlink()
    path.write_bytes(original + b'{"incomplete":')
    reader = SessionReader(root, "indexed")
    assert reader.journal_index().committed == 2
    assert path.read_bytes() == original + b'{"incomplete":'
    with SessionJournal(tmp_path, "indexed") as journal:
        assert path.read_bytes() == original
        journal.append("probe.three", {}, state())
    assert [json.loads(line)["sequence"] for line in path.read_bytes().splitlines()] == [1, 2, 3]


@pytest.mark.parametrize("change", ["legacy", "append", "rewrite", "replace"])
def test_stale_or_legacy_cache_rebuilds_from_current_log(tmp_path, recorded, change):
    root, directory = recorded
    path, cache = directory / "events.jsonl", directory / "events.idx"
    original = path.read_bytes()
    row = json.loads(original.splitlines()[0])
    row.update(sequence=3, kind="probe.three")
    extra = (json.dumps(row) + "\n").encode()
    if change == "legacy":
        lines = cache.read_text().splitlines(keepends=True)
        lines[0] = lines[0].replace("index.v3", "index.v2")
        cache.write_text("".join(lines[:3]))
    elif change == "append":
        with path.open("ab") as stream:
            stream.write(extra)
    elif change == "rewrite":
        # Same inode, growing size, different historical row boundaries.
        rows = original.splitlines(keepends=True)
        changed = json.loads(rows[0])
        changed["payload"]["padding"] = "x" * 400
        path.write_bytes((json.dumps(changed) + "\n").encode() + rows[1] + extra)
    else:
        replacement = directory / "replacement"
        replacement.write_bytes(original + extra)
        replacement.chmod(0o600)
        replacement.replace(path)
    expected = path.read_bytes()
    count = 2 if change == "legacy" else 3
    assert SessionReader(root, "indexed").journal_index().committed == count
    with SessionJournal(tmp_path, "indexed") as journal:
        assert len(journal.events()) == count
        assert path.read_bytes() == expected


@pytest.mark.parametrize("tail", [b'{"bad":}\n', b'[]\n', b'\xff\n', b'\n'])
def test_terminated_bad_tail_is_never_removed(tmp_path, recorded, tail):
    _, directory = recorded
    path = directory / "events.jsonl"
    original = path.read_bytes() + tail + b'{"unfinished":'
    path.write_bytes(original)
    with pytest.raises(ValueError):
        SessionJournal(tmp_path, "indexed")
    assert path.read_bytes() == original


@pytest.mark.parametrize("tail", [b'{"unfinished":', b'{"complete_json":true}'])
def test_first_unterminated_record_recovers_to_empty_log(tmp_path, tail):
    with SessionJournal(tmp_path, "empty") as journal:
        path = journal.directory / "events.jsonl"
    path.write_bytes(tail)
    with SessionJournal(tmp_path, "empty") as journal:
        assert path.read_bytes() == b""
        journal.append("probe.one", {}, state())
        assert journal.events()[0]["sequence"] == 1
