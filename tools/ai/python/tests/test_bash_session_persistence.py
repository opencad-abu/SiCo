"""Actual local Bash output uses session storage and reopens without reexecution."""

from dataclasses import replace
import hashlib
import shlex
import sys

from bash_channel_fixtures import command
from bash_journal_fixtures import LocalBashBackend
from session_terminal_fixtures import CLAIM, CONTEXT
from sico.core.terminal_output import OutputChunk
from sico.process_identity import boot_id
from sico.service.terminal_dispatcher import TerminalSessionDispatcher
from sico.storage.history_reader import SessionReader
from sico.storage.journal import SessionJournal
from sico.storage.terminal_records import JOURNAL_KIND, restore


def test_real_shell_output_survives_close_and_reopen_without_creating_a_shell(tmp_path):
    work = tmp_path / "work space"
    work.mkdir()
    (work / "next").mkdir()
    claim = replace(CLAIM, cwd=str(work))
    backend = LocalBashBackend()
    first = command("cd next; export SICO_FIXTURE=kept; fixture() { printf function; }; printf first")
    expected = ("中文\0\x1b[31m\n" * 50000).encode() + b"\xff\xfe"
    script = "import os; data=('中文\\0\\x1b[31m\\n'*50000).encode()+b'\\xff\\xfe'; " \
             "os.write(1,data); os.write(2,b'error-stream')"
    second = command(f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}", "second")
    third = command("printf '%s:%s:' \"$PWD\" \"$SICO_FIXTURE\"; fixture; "
                    "(sleep .05; printf late) & printf now", "third")
    commands = (first, second, third)
    states = []
    with SessionJournal(tmp_path, "session") as journal:
        owner = TerminalSessionDispatcher(journal, CONTEXT, claim, backend=backend,
                                          host_id="local-fixture", boot_id=boot_id())
        try:
            for request in commands:
                owner.submit(request)
                state = owner.wait(request.ref, 8)
                assert state.succeeded and state.output_complete
                states.append(state)
            assert backend.starts == 1
            assert len(backend.generations) == 3 and len(set(backend.generations)) == 1
        finally:
            owner.close()
        root = journal.root
    reader = SessionReader(root, "session")
    output = {request.ref.command_id: dict(stdout=bytearray(), stderr=bytearray()) for request in commands}
    for row in reader.stream_events():
        if row["kind"] == JOURNAL_KIND:
            fact = restore(row["payload"], reader)
            if fact["kind"] == "output":
                chunk = OutputChunk.from_record(fact["chunk"])
                output[chunk.ref.command_id][chunk.stream].extend(chunk.data)
    assert output["first"]["stdout"] == b"first"
    assert hashlib.sha256(output["second"]["stdout"]).digest() == hashlib.sha256(expected).digest()
    assert output["second"]["stderr"] == b"error-stream"
    assert output["third"]["stdout"] == f"{work / 'next'}:kept:functionnowlate".encode()
    reopened_backend = LocalBashBackend()
    with SessionJournal(tmp_path, "session") as journal:
        owner = TerminalSessionDispatcher(journal, CONTEXT, claim, backend=reopened_backend,
                                          host_id="local-fixture", boot_id=boot_id())
        for request, previous in zip(commands, states):
            assert owner.wait(request.ref, 0) == previous
            assert owner.submit(request) == request.ref
        owner.close()
    assert reopened_backend.starts == 0


def test_shell_exit_preserves_captured_tail_without_claiming_complete_output(tmp_path):
    claim = replace(CLAIM, cwd=str(tmp_path))
    backend = LocalBashBackend()
    request = command("printf before-exit; printf error-tail >&2; exit 9")
    with SessionJournal(tmp_path, "session") as journal:
        owner = TerminalSessionDispatcher(journal, CONTEXT, claim, backend=backend,
                                          host_id="local-fixture", boot_id=boot_id())
        try:
            owner.submit(request)
            state = owner.wait(request.ref, 5)
            assert state.phase == "unknown" and not state.output_complete
        finally:
            owner.close()
        root = journal.root
    output = dict(stdout=bytearray(), stderr=bytearray())
    reader = SessionReader(root, "session")
    for row in reader.stream_events():
        if row["kind"] == JOURNAL_KIND:
            fact = restore(row["payload"], reader)
            if fact["kind"] == "output":
                chunk = OutputChunk.from_record(fact["chunk"])
                output[chunk.stream].extend(chunk.data)
    assert output == dict(stdout=b"before-exit", stderr=b"error-tail")
