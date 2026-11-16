"""The public query entry reads actual managed output without recreating a shell."""

import base64
import hashlib
import shlex
import sys

from exec_live_fixtures import managed_configuration
from test_managed_bash_backend_live import managed_backend
from session_terminal_fixtures import CONTEXT
from bash_channel_fixtures import command
from sico.execution.managed_bash import ManagedBashBackend
from sico.process_identity import boot_id
from sico.service.terminal_dispatcher import TerminalSessionDispatcher
from sico.storage.journal import SessionJournal
from terminal_history_fixtures import history


def test_managed_history_queries_reopen_binary_output_without_an_execution_path(managed_backend, tmp_path, monkeypatch):
    backend, claim = managed_backend()
    expected = ('中文\0\x1b[31m\n'*50000).encode() + b'\xff\xfe'
    script = "import os; os.write(1,('中文\\0\\x1b[31m\\n'*50000).encode()+b'\\xff\\xfe'); os.write(2,b'error\\0\\xff')"
    request = command(shlex.join([sys.executable, '-c', script]))
    with SessionJournal(tmp_path, 'session') as journal:
        owner = TerminalSessionDispatcher(journal, CONTEXT, claim, backend=backend,
                                          host_id='history-fixture', boot_id=boot_id())
        try:
            owner.submit(request)
            state = owner.wait(request.ref, 15)
            assert state.succeeded and state.output_complete
        finally:
            owner.close()
        root = journal.root
    def forbidden(*args, **kwargs):
        raise AssertionError('History attempted process creation')
    monkeypatch.setattr(ManagedBashBackend, 'start', forbidden)
    monkeypatch.setattr(SessionJournal, '__init__', forbidden)
    query, _ = history(root)
    assert query.commands()['commands'][0]['ref'] == request.ref.record()
    assert query.status(request.ref)['command']['output_bytes'] == len(expected) + 7
    reply = query.receipt(request.ref)
    assert reply['has_more'] and reply['next_offset'] <= 768
    cursor, streams = 0, dict(stdout=bytearray(), stderr=bytearray())
    while True:
        page = query.output(request.ref, offset=cursor)
        for part in page['fragments']:
            streams[part['stream']].extend(base64.b64decode(part['data']))
        cursor = page['next_offset']
        if not page['has_more']: break
    assert hashlib.sha256(streams['stdout']).digest() == hashlib.sha256(expected).digest()
    assert streams['stderr'] == b'error\0\xff'
