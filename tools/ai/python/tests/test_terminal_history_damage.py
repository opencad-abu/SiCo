"""Persisted metadata and attachment identity failures never produce a success page."""

import json

import pytest

from sico.storage.journal import SessionJournal
from sico.storage.terminal_history import TerminalHistory
from terminal_history_fixtures import history, recorded


def rewrite(path, change):
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    change(rows)
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))


@pytest.mark.parametrize('damage', ['sequence', 'offset', 'stream', 'size', 'watermark',
                                    'boolean', 'reference', 'hash', 'contract', 'execution'])
def test_invalid_metadata_is_rejected_even_without_loading_bytes(tmp_path, monkeypatch, damage):
    with SessionJournal(tmp_path, 'session') as journal:
        command, _ = recorded(journal, chunks=[('stdout', b'abc')])
        root, path = journal.root, journal.directory / 'events.jsonl'
    def change(rows):
        value = rows[2]['payload']
        chunk = value['chunk']
        if damage == 'sequence': chunk['sequence'] = 2
        elif damage == 'offset': chunk['offset'] = 1
        elif damage == 'stream': chunk['stream'] = 'pty'
        elif damage == 'size': chunk['size'] = 0
        elif damage == 'boolean': chunk['size'] = True
        elif damage == 'reference': chunk['artifact']['path'] = chunk['artifact']['path'].replace('/session/', '/other/')
        elif damage == 'hash': chunk['artifact']['sha256'] = 'bad'
        elif damage == 'contract': value['contract'] = 'unsupported'
        elif damage == 'execution': rows[1]['payload']['ref']['command_id'] = 'unrelated'
        else: rows[3]['payload']['output_bytes'] = 4
    rewrite(path, change)
    query, reader = history(root)
    monkeypatch.setattr(reader, 'artifact_text', lambda *a, **k: pytest.fail('Unexpected byte read'))
    for operation in (query.status, query.output):
        with pytest.raises(ValueError): operation(command.ref)
    with pytest.raises(ValueError): query.commands()


def test_other_command_attachment_cannot_be_substituted(tmp_path):
    with SessionJournal(tmp_path, 'session') as journal:
        first, _ = recorded(journal, chunks=[('stdout', b'abc')])
        _, other = recorded(journal, name='other', chunks=[('stdout', b'xyz')])
        root, path = journal.root, journal.directory / 'events.jsonl'
    rewrite(path, lambda rows: rows[2]['payload']['chunk'].update(artifact=other[0]))
    query, _ = history(root)
    assert query.status(first.ref)['integrity'] == 'metadata_only'
    with pytest.raises(ValueError, match='identity'):
        query.output(first.ref)


@pytest.mark.parametrize('raw', [b'not-json\n', b'{"kind":"broken"}\n'])
def test_complete_corrupt_record_is_never_an_ignored_tail(tmp_path, raw):
    with SessionJournal(tmp_path, 'session') as journal:
        command, _ = recorded(journal)
        root, path = journal.root, journal.directory / 'events.jsonl'
    with path.open('ab') as stream: stream.write(raw)
    query, _ = history(root)
    with pytest.raises(ValueError): query.status(command.ref)


def test_reader_created_before_deletion_cannot_keep_reading(tmp_path):
    with SessionJournal(tmp_path, 'session') as journal:
        command, _ = recorded(journal)
        root = journal.root
    query, _ = history(root)
    directory = root / 'removed-records'
    directory.mkdir(mode=0o700)
    path = directory / 'session.json'
    path.write_text(json.dumps(dict(contract='sico.record.removed.v1', session_id='session',
        version='a'*64, external_outcome='unconfirmed')))
    path.chmod(0o600)
    with pytest.raises(ValueError, match='已删除'): query.status(command.ref)


def test_empty_snapshot_and_accepted_without_start_are_safe_to_browse(tmp_path):
    from terminal_history_fixtures import append, request
    from sico.core.terminal_state import event
    with SessionJournal(tmp_path, 'session') as journal:
        query = TerminalHistory(journal)
        assert query.commands()['commands'] == []
        command = request()
        append(journal, event('accepted', command.ref, request=command.record()))
        reply = query.receipt(command.ref)
        assert reply['command']['phase'] == 'unknown' and reply['preview'] == []
