"""Bounded terminal history survives closure without execution or eager output reads."""

import base64
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tracemalloc

import pytest

from sico.core.terminal_output import MAX_CHUNK_BYTES, OutputChunk
from sico.core.terminal_state import event
from sico.storage.journal import SessionJournal
from sico.storage.terminal_history import TerminalHistory
from sico.storage.terminal_records import JOURNAL_KIND, store
from terminal_history_fixtures import append, history, recorded, request


def test_fifty_thousand_lines_reassemble_after_close_with_only_selected_artifacts(tmp_path, monkeypatch):
    data = ('中文\0\x1b[31m\n' * 50000).encode() + b'\xff\xfe'
    chunks = [('stderr' if n % 2 else 'stdout', data[i:i+MAX_CHUNK_BYTES])
              for n, i in enumerate(range(0, len(data), MAX_CHUNK_BYTES))]
    with SessionJournal(tmp_path, 'session') as journal:
        command, artifacts = recorded(journal, chunks=chunks, code=7)
        root = journal.root
    query, reader = history(root)
    original, reads = reader.artifact_text, []
    def read(path, *args, **kwargs):
        reads.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(reader, 'artifact_text', read)
    listing = query.commands()
    assert listing['commands'][0]['exit_code'] == 7 and listing['integrity'] == 'metadata_only'
    assert query.status(command.ref)['command']['output_complete'] and not reads
    digest, cursor = hashlib.sha256(), 0
    tracemalloc.start()
    try:
        while cursor < len(data):
            reads.clear()
            page = query.output(command.ref, offset=cursor, limit=16001)
            assert len(reads) <= 2 and page['next_offset'] > cursor
            raw = b''.join(base64.b64decode(part['data']) for part in page['fragments'])
            assert raw == data[cursor:page['next_offset']]
            assert len(raw) <= 16001 and len(json.dumps(page, ensure_ascii=False).encode()) < 32768
            digest.update(raw)
            cursor = page['next_offset']
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024 * 1024
    assert digest.digest() == hashlib.sha256(data).digest()
    assert page['has_more'] is False and page['command']['output_complete']
    assert len(artifacts) > 1


def test_cross_stream_slices_fragment_budget_and_polling_cursor(tmp_path):
    with SessionJournal(tmp_path, 'session') as journal:
        command, _ = recorded(journal, chunks=[('stdout', b'abc'), ('stderr', b'def'),
                                               ('stdout', b'g')], finish=False)
        query = TerminalHistory(journal)
        first = query.output(command.ref, offset=1, limit=5, fragments=1)
        assert first['next_offset'] == 3 and first['has_more']
        assert first['fragments'] == [dict(stream='stdout', offset=1, encoding='base64', data='YmM=')]
        second = query.output(command.ref, offset=3, limit=4)
        assert [p['stream'] for p in second['fragments']] == ['stderr', 'stdout']
        assert second['command']['phase'] == 'unknown' and not second['command']['output_complete']
        end = query.output(command.ref, offset=7)
        assert not end['fragments'] and not end['has_more']
        append(journal, event('output', command.ref,
                             chunk=OutputChunk(command.ref, 4, 7, 'stderr', b'tail').record()))
        assert query.output(command.ref, offset=7)['next_offset'] == 11


def test_command_pages_bound_identity_and_do_not_open_attachments(tmp_path, monkeypatch):
    with SessionJournal(tmp_path, 'session') as journal:
        commands = [recorded(journal, name='c' + str(i), chunks=[('stdout', b'x')])[0]
                    for i in range(23)]
        root = journal.root
    query, reader = history(root)
    monkeypatch.setattr(reader, 'artifact_text', lambda *a, **k: pytest.fail('Eager output read'))
    first = query.commands()
    second = query.commands(after=first['next_after'])
    assert [row['ref'] for row in first['commands'] + second['commands']] == [c.ref.record() for c in commands]
    assert len(first['commands']) == 20 and second['next_after'] is None
    for row in first['commands']:
        assert 'request' not in row and 'generation' not in row and 'execution_id' not in row
    assert query.commands(after=second['observed_through'])['commands'] == []


def test_history_page_carries_bounded_request_metadata_and_cursor(tmp_path):
    with SessionJournal(tmp_path, 'session') as journal:
        first, _ = recorded(journal, name='history-one', text='pwd')
        second, _ = recorded(journal, name='history-two', text='printf ' + 'x' * 300)
        root = journal.root
    query, _ = history(root)
    page = query.history(limit=1)
    assert page['contract'] == 'sico.terminal.commands.v2'
    assert page['next_after'] is not None and page['observed_through'] >= 3
    row = page['commands'][0]
    assert row['ref'] == first.ref.record() and row['actor'] == 'model'
    assert row['mode'] == 'pipes' and row['command'] == 'pwd'
    assert row['timestamp'] and not row['command_truncated']
    rest = query.history(after=page['next_after'], limit=1)
    assert [item['ref'] for item in rest['commands']] == [second.ref.record()]
    assert rest['commands'][0]['command_truncated'] is True
    assert rest['commands'][0]['command'].startswith('printf ')


def test_history_page_stays_within_wire_budget_for_escaped_commands(tmp_path):
    with SessionJournal(tmp_path, 'session') as journal:
        for number in range(20):
            recorded(journal, name='bounded-' + str(number), text='\x01' * 300)
        root = journal.root
    query, _ = history(root)
    page = query.history(limit=20)
    assert len(page['commands']) == 20
    assert all(row['command_truncated'] for row in page['commands'])
    assert len(json.dumps(page, ensure_ascii=False).encode('utf-8')) <= 32768


def test_repeated_admission_is_not_a_second_command(tmp_path):
    with SessionJournal(tmp_path, 'session') as journal:
        command, _ = recorded(journal)
        append(journal, event('accepted', command.ref, request=command.record()))
        root = journal.root
    query, _ = history(root)
    first = query.commands(limit=1)
    second = query.commands(limit=1, after=first['next_after'])
    assert len(first['commands']) == 1 and second['commands'] == [] and second['next_after'] is None


def test_snapshot_does_not_mix_in_appends_between_metadata_passes(tmp_path, monkeypatch):
    import sico.storage.terminal_history as module
    with SessionJournal(tmp_path, 'session') as journal:
        recorded(journal)
        query = TerminalHistory(journal)
        original = module._Snapshot.events
        appended = []
        def events(snapshot):
            yield from original(snapshot)
            if not appended:
                appended.append(recorded(journal, name='later')[0])
        monkeypatch.setattr(module._Snapshot, 'events', events)
        first = query.commands()
        assert len(first['commands']) == 1 and first['observed_through'] == 3
        assert len(query.commands()['commands']) == 2


def test_incomplete_tail_and_orphan_output_are_never_promoted(tmp_path):
    with SessionJournal(tmp_path, 'session') as journal:
        command, _ = recorded(journal, chunks=[('stdout', b'kept')], finish=False)
        orphan = store(event('output', command.ref,
            chunk=OutputChunk(command.ref, 2, 4, 'stderr', b'orphan').record()), journal)
        root, path = journal.root, journal.directory / 'events.jsonl'
    with path.open('ab') as stream:
        stream.write(b'{"partial":')
    query, _ = history(root)
    page = query.output(command.ref)
    assert page['command']['phase'] == 'unknown' and page['next_offset'] == 4
    assert (root / orphan['chunk']['artifact']['path']).exists()
    assert path.read_bytes().endswith(b'{"partial":')


@pytest.mark.parametrize('damage', ['missing', 'corrupt', 'symlink'])
def test_unread_damage_does_not_hide_metadata_but_selected_damage_fails(tmp_path, damage):
    with SessionJournal(tmp_path, 'session') as journal:
        command, artifacts = recorded(journal, chunks=[('stdout', b'first'), ('stderr', b'second')])
        root = journal.root
    path = root / artifacts[1]['path']
    if damage == 'missing': path.unlink()
    elif damage == 'corrupt': path.write_bytes(b'broken')
    else:
        path.unlink()
        path.symlink_to(root / artifacts[0]['path'])
    query, _ = history(root)
    assert query.status(command.ref)['integrity'] == 'metadata_only'
    assert query.output(command.ref, limit=5)['next_offset'] == 5
    with pytest.raises((ValueError, OSError)):
        query.output(command.ref, offset=5)


@pytest.mark.parametrize('operation,kwargs', [
    ('commands', {'limit': 0}), ('commands', {'limit': 21}), ('commands', {'after': True}),
    ('commands', {'after': 999}), ('output', {'offset': -1}), ('output', {'offset': 999}),
    ('output', {'limit': 16385}), ('output', {'limit': True}), ('output', {'fragments': 33}),
])
def test_invalid_queries_fail_before_artifact_read(tmp_path, monkeypatch, operation, kwargs):
    with SessionJournal(tmp_path, 'session') as journal:
        command, _ = recorded(journal)
        root = journal.root
    query, reader = history(root)
    monkeypatch.setattr(reader, 'artifact_text', lambda *a, **k: pytest.fail('Unexpected artifact read'))
    with pytest.raises(ValueError):
        getattr(query, operation)(*(() if operation == 'commands' else (command.ref,)), **kwargs)
    with pytest.raises(ValueError): query.status(replace(command.ref, session_id='other'))
    with pytest.raises(KeyError): query.status(replace(command.ref, command_id='absent'))


def test_queries_do_not_write_cache_or_attach_execution(tmp_path, monkeypatch):
    import sico.storage.journal as module
    with SessionJournal(tmp_path, 'session') as journal:
        command, _ = recorded(journal, chunks=[('stdout', b'history')])
        root = journal.root
    before = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in root.rglob('*') if p.is_file()}
    monkeypatch.setattr(module.SessionJournal, '__init__', lambda *a, **k: pytest.fail('Writer created'))
    query, _ = history(root)
    query.commands(); query.status(command.ref); query.output(command.ref); query.receipt(command.ref)
    after = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in root.rglob('*') if p.is_file()}
    assert after == before
