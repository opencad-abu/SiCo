"""Real local Bash command boundaries; managed-sandbox integration is separate."""

import os
import shlex
import sys
import time
from dataclasses import replace

import pytest

from bash_channel_fixtures import bash_session, command
from sico.terminal.bash_control import BashControl, MAX_CONTROL_BYTES
from sico.terminal.command_pipes import CommandPipes


def test_same_shell_preserves_state_and_reports_actual_directory(bash_session, tmp_path):
    target = tmp_path / "中文 with space"
    target.mkdir()
    with bash_session() as shell:
        first, streams = shell.run(command("export SICO_VALUE=kept; fixture() { printf function; }; "
                                          "unset HOME; printf '%s' \"$$\"", cwd=str(target)))
        pid = int(streams["stdout"])
        assert first[0].value == pid and first[0].cwd == str(target)
        second, streams = shell.run(command("printf '%s:%s:%s:' \"$$\" \"$PWD\" \"$SICO_VALUE\"; "
                                           "fixture; printf ':%s' \"${HOME-unset}\"", "second"))
        assert streams["stdout"] == f"{pid}:{target}:kept:function:unset".encode()
        assert second[0].value == pid and second[-1].value == 0


def test_command_output_cannot_forge_completion_and_binary_bytes_survive(bash_session):
    with bash_session() as shell:
        facts, streams = shell.run(command("printf '%s\\0' finished first 0 /; printf '\\377'; "
                                          "printf error >&2; false"))
        assert [fact.kind for fact in facts] == ["started", "finished"]
        assert facts[-1].value == 1
        assert streams["stdout"] == b"finished\0first\0" + b"0\0/\0\xff"
        assert streams["stderr"] == b"error"


def test_late_background_bytes_remain_in_their_original_command_pipe(bash_session):
    with bash_session() as shell:
        first, streams = shell.run(command("(sleep .1; printf late; printf late-error >&2) & printf now"))
        assert streams == dict(stdout=b"nowlate", stderr=b"late-error")
        second, streams = shell.run(command("printf next", "second"))
        assert streams == dict(stdout=b"next", stderr=b"")
        assert first[0].value == second[0].value


def test_late_output_is_still_separate_while_next_command_is_running(bash_session):
    def finished(shell):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                raw = os.read(shell.child.stdout.fileno(), 65536)
            except BlockingIOError:
                time.sleep(.002)
                continue
            assert raw
            if any(row.kind == "finished" for row in shell.control.feed(raw)):
                return
        pytest.fail("No command boundary")
    with bash_session() as shell:
        first = shell.submit(command("(sleep .05; printf first-late) & printf first-now"))
        finished(shell)
        first.release_writers()
        second = shell.submit(command("printf second-now; sleep .1; printf second-end", "second"))
        finished(shell)
        second.release_writers()
        streams = []
        for pipes in (first, second):
            data = bytearray()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                value = pipes.read("stdout")
                if value == b"":
                    break
                if value:
                    data.extend(value)
                else:
                    time.sleep(.002)
            else:
                pytest.fail("Command output did not drain")
            streams.append(bytes(data))
        assert streams == [b"first-nowfirst-late", b"second-nowsecond-end"]


def test_failed_directory_change_rejects_before_user_code_and_shell_remains_usable(bash_session, tmp_path):
    with bash_session() as shell:
        facts, streams = shell.run(command("printf must-not-run", cwd=str(tmp_path / "missing")))
        assert [fact.kind for fact in facts] == ["rejected"] and facts[0].value != 0
        assert not streams["stdout"] and streams["stderr"]
        facts, streams = shell.run(command("printf usable", "second"))
        assert streams["stdout"] == b"usable" and facts[-1].value == 0


@pytest.mark.parametrize("text,code", [("return 7", 7), ("if then", 2)])
def test_nonzero_and_syntax_errors_are_real_shell_results(bash_session, text, code):
    with bash_session() as shell:
        facts, _ = shell.run(command(text))
        assert facts[-1].value == code
        facts, streams = shell.run(command("printf usable", "second"))
        assert facts[-1].value == 0 and streams["stdout"] == b"usable"


@pytest.mark.parametrize("text", ["exit 9", "exec /bin/true", "set -e; false"])
def test_exit_exec_and_errexit_never_invent_a_ready_shell(bash_session, text):
    with bash_session() as shell:
        with pytest.raises(ValueError, match="before command completion"):
            shell.run(command(text))


def test_command_stdin_is_not_the_shell_control_input(bash_session):
    with bash_session() as shell:
        facts, streams = shell.run(command("cat; printf after-cat"))
        assert streams["stdout"] == b"after-cat" and facts[-1].value == 0
        _, streams = shell.run(command("printf next", "second"))
        assert streams["stdout"] == b"next"


def test_argv_literals_are_not_evaluated_as_shell_programs(bash_session):
    value = "中文 'quotes' $(printf unexpected) `printf unexpected`\nnext line"
    request = replace(command(), shell=None, text=None, argv=("/bin/printf", "%s", value))
    with bash_session() as shell:
        facts, streams = shell.run(request)
        assert facts[-1].value == 0 and streams["stdout"] == value.encode()


def test_control_descriptor_is_closed_during_user_code_and_child_exec(bash_session):
    script = "import os; os.fstat(9)"
    with bash_session() as shell:
        _, streams = shell.run(command(f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}; "
                                       "printf cannot-forge >&9"))
        assert not streams["stdout"] and b"Bad file descriptor" in streams["stderr"]


def test_command_payload_is_prepared_only_once_even_after_uncertain_delivery():
    pipes = CommandPipes(command())
    try:
        assert pipes.dispatch(9)
        with pytest.raises(ValueError):
            pipes.dispatch(9)
    finally:
        pipes.close()


def test_sealed_source_and_all_descriptors_close_on_failure(monkeypatch):
    original = os.pipe
    original_memfd = os.memfd_create
    captured = []
    source = []
    def memfd(*args):
        descriptor = original_memfd(*args)
        source.append(descriptor)
        return descriptor
    def failing_pipe():
        if captured:
            raise OSError("injected pipe failure")
        ends = original()
        captured.extend(ends)
        return ends
    monkeypatch.setattr(os, "pipe", failing_pipe)
    monkeypatch.setattr(os, "memfd_create", memfd)
    with pytest.raises(OSError, match="injected"):
        CommandPipes(command())
    for descriptor in captured + source:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_source_cannot_be_rewritten_and_cleanup_is_idempotent():
    pipes = CommandPipes(command())
    try:
        with pytest.raises(OSError):
            os.write(pipes._source, b"different command")
    finally:
        pipes.close()
        pipes.close()


def test_control_handles_fragmentation_but_rejects_wrong_command_and_missing_tail():
    decoder = BashControl()
    decoder.expect("first")
    observations = []
    for byte in b"started\0first\0" + b"123\0/\0finished\0first\0" + b"0\0/\0":
        observations.extend(decoder.feed(bytes([byte])))
    assert [row.kind for row in observations] == ["started", "finished"]
    decoder.expect("second")
    with pytest.raises(ValueError, match="another command"):
        decoder.feed(b"started\0first\0" + b"123\0/\0")
    with pytest.raises(ValueError):
        decoder.eof()


def test_two_maximum_directory_frames_can_arrive_in_one_read():
    directory = b"/" + b"a" * 4095
    decoder = BashControl()
    decoder.expect("first")
    rows = decoder.feed(b"started\0first\0" + b"123\0" + directory + b"\0"
                        + b"finished\0first\0" + b"0\0" + directory + b"\0")
    assert [row.kind for row in rows] == ["started", "finished"]


@pytest.mark.parametrize("raw", [b"x" * (MAX_CONTROL_BYTES + 1),
    b"finished\0first\0" + b"0\0/\0", b"started\0first\0-1\0/\0",
    b"started\0first\0" + b"2147483648\0/\0"])
def test_invalid_control_fails_closed(raw):
    decoder = BashControl()
    decoder.expect("first")
    with pytest.raises(ValueError):
        decoder.feed(raw)
