"""Wire-size budgets count escaping, multibyte text, identities and fragmentation."""

import base64
from dataclasses import replace
import json

from sico.core import terminal_receipts as views
from sico.core.terminal_commands import CommandRef
from sico.core.terminal_output import OutputChunk
from sico.core.terminal_state import CommandState
from sico.storage.journal import SessionJournal
from terminal_history_fixtures import history, recorded, request


def encoded(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode())


def worst_state():
    req = request(ref=CommandRef('s'*96, 't'*96, 'c'*96))
    return replace(CommandState(req), phase='unknown', output_sequence=(1 << 63)-1,
                   output_bytes=(1 << 63)-1, reason='\x01'*1024)


def test_maximum_command_list_including_escaped_reasons_fits_budget():
    row = views.summary(worst_state())
    listing = dict(contract='sico.terminal.commands.v1', commands=[row]*views.MAX_COMMANDS,
                   next_after=(1 << 63)-1, observed_through=(1 << 63)-1, integrity='metadata_only')
    assert row['reason_truncated'] and encoded(listing) <= views.MAX_PAGE_JSON
    assert views.bounded(listing, views.MAX_PAGE_JSON) is listing


def test_maximum_binary_page_and_worst_preview_are_bounded():
    state = worst_state()
    raw = b'\x01' * views.MAX_PAGE_BYTES
    parts = []
    for offset in range(0, len(raw), 512):
        chunk = OutputChunk(state.request.ref, offset+1, offset, 'stderr', raw[offset:offset+512])
        parts.append(views.fragment(chunk, offset, chunk.end))
    page = dict(contract='sico.terminal.output.page.v1', command=views.summary(state),
                observed_through=(1 << 63)-1, offset=0, next_offset=len(raw), has_more=True,
                fragments=parts, integrity='returned_chunks_verified')
    assert len(parts) == views.MAX_FRAGMENTS and encoded(page) <= views.MAX_PAGE_JSON
    preview = dict(page, fragments=[dict(stream='stderr', offset=i*96, encoding='base64',
                   data=base64.b64encode(b'\x01'*96).decode()) for i in range(8)], next_offset=768)
    assert encoded(views.receipt(preview)) <= views.MAX_RECEIPT_JSON


def test_preview_marks_lossy_decoding_and_preserves_exact_read_cursor(tmp_path):
    with SessionJournal(tmp_path, 'session') as journal:
        command, _ = recorded(journal, chunks=[('stdout', b'\xff' + '中文'.encode()*1000)])
        root = journal.root
    query, _ = history(root)
    reply = query.receipt(command.ref)
    assert reply['preview'][0]['decoding_lossy'] and reply['has_more']
    assert reply['next_offset'] == 768 and encoded(reply) <= views.MAX_RECEIPT_JSON
    page = query.output(command.ref, offset=reply['next_offset'])
    assert page['offset'] == 768 and page['next_offset'] == 6001
    assert 'artifact' not in json.dumps(reply) and 'request' not in reply['command']


def test_maximum_escaped_command_is_retrievable_in_bounded_exact_request_pages(tmp_path):
    with SessionJournal(tmp_path, 'session') as journal:
        command, _ = recorded(journal, text='\x01' * 65536)
        root = journal.root
    query, _ = history(root)
    cursor, data = 0, bytearray()
    while True:
        page = query.request(command.ref, offset=cursor)
        assert encoded(page) <= views.MAX_PAGE_JSON and page['next_offset'] > cursor
        data.extend(base64.b64decode(page['data']))
        cursor = page['next_offset']
        if not page['has_more']: break
    assert json.loads(data) == command.record() and cursor == len(data)
    assert query.request(command.ref, offset=cursor)['data'] == ''
