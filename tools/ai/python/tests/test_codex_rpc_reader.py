"""Stdio reader cancellation and framing without external app-server timing."""

import os
import queue
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from sico.codex import rpc
from sico.diagnostics import DiagnosticLog


@pytest.fixture
def pipe_reader(monkeypatch, tmp_path):
    monkeypatch.setattr(rpc, "MAX_LINE", 64)
    read_fd, write_fd = os.pipe()
    server = object.__new__(rpc.AppServer)
    server.stopped = threading.Event()
    server.closed_by_client = False
    server.exit = None
    server._failure_lock = threading.Lock()
    server._failure = None
    server._diagnostic = DiagnosticLog(
        tmp_path / "rpc.jsonl", component="rpc-test")
    server.diagnostic_id = server._diagnostic.correlation_id
    server.messages = queue.Queue(maxsize=2)
    server.child = SimpleNamespace(stdout=os.fdopen(read_fd, "rb", buffering=0))
    server.reader = threading.Thread(target=server._read)
    server.reader.start()
    try:
        yield server, write_fd
    finally:
        server.stopped.set()
        os.close(write_fd)
        server.reader.join(2)
        server.child.stdout.close()
        assert not server.reader.is_alive()


@pytest.mark.parametrize("tail", [b"", b'{"unfinished":'])
def test_close_retires_reader_even_if_another_writer_keeps_pipe_open(pipe_reader, tail):
    server, write_fd = pipe_reader
    os.write(write_fd, b'{"ready":true}\n' + tail)
    assert server.messages.get(timeout=2) == {"ready": True}
    cleanup = []
    server._process = SimpleNamespace(close=lambda: cleanup.append(True))
    server.close()
    assert cleanup == [True]
    assert not server.reader.is_alive()
    assert server.child.stdout.closed


def test_fragmented_and_combined_frames_keep_order(pipe_reader):
    server, write_fd = pipe_reader
    for chunk in (b'{"n":', b'1}\n{"n":2}\n'):
        os.write(write_fd, chunk)
    assert server.messages.get(timeout=2) == {"n": 1}
    assert server.messages.get(timeout=2) == {"n": 2}


@pytest.mark.parametrize("payload,code", [
    (b'{"x":1,"x":2}\n', "protocol_frame_invalid"),
    (b'{}' + b' ' * 63, "protocol_frame_too_large"),
    (b' ' * 65 + b'\n', "protocol_frame_too_large"),
])
def test_invalid_or_oversize_frame_stops_reader(pipe_reader, payload, code):
    server, write_fd = pipe_reader
    os.write(write_fd, payload)
    assert server.stopped.wait(2)
    assert server.messages.empty()
    with pytest.raises(rpc.RpcError) as caught:
        server._next(0.01)
    assert caught.value.code == code
    assert caught.value.diagnostic_id == server.diagnostic_id


def test_full_message_queue_remains_cancellable(pipe_reader):
    server, write_fd = pipe_reader
    os.write(write_fd, b'{}\n{}\n{}\n')
    deadline = time.monotonic() + 2
    while not server.messages.full():
        assert time.monotonic() < deadline
        time.sleep(.005)
    server.stopped.set()
    server.reader.join(.5)
    assert not server.reader.is_alive()


def test_frame_at_limit_is_accepted(pipe_reader):
    server, write_fd = pipe_reader
    os.write(write_fd, b'{"s":"' + b'x' * 55 + b'"}\n')
    assert server.messages.get(timeout=2) == {"s": "x" * 55}


def test_partial_eof_is_not_published(pipe_reader):
    server, write_fd = pipe_reader
    os.write(write_fd, b'{"unfinished":')
    # Closing a duplicate alone would not produce EOF; replace the fixture's
    # writer with a harmless descriptor so its finalizer can still close it.
    replacement = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(replacement, write_fd)
    finally:
        os.close(replacement)
    assert server.stopped.wait(2)
    assert server.messages.empty()
    with pytest.raises(rpc.RpcError) as caught:
        server._next(0.01)
    assert caught.value.code == "protocol_frame_truncated"
    assert caught.value.diagnostic_id == server.diagnostic_id


def test_disconnect_reason_reports_the_exit_evidence():
    server = object.__new__(rpc.AppServer)
    server.closed_by_client = False
    server.exit = None
    server.child = SimpleNamespace(poll=lambda: -9)
    assert server.disconnect_reason() == "Codex app-server disconnected (signal 9)"
    server.exit = 137
    assert server.disconnect_reason().endswith("(exit 137)")
    server.closed_by_client = True
    assert server.disconnect_reason().endswith("(client closed)")
    server.closed_by_client = False
    server.exit = None
    server.child = SimpleNamespace(poll=lambda: None)
    assert server.disconnect_reason().endswith("(process still exiting)")


def test_next_reports_the_exit_status_once_the_reader_stopped():
    server = object.__new__(rpc.AppServer)
    server.messages = queue.Queue()
    server.stopped = threading.Event()
    server.stopped.set()
    server.closed_by_client = False
    server.exit = 137
    server.child = SimpleNamespace(poll=lambda: 137)
    with pytest.raises(rpc.RpcError) as exc:
        server._next(0.01)
    assert "(exit 137)" in str(exc.value)


def test_close_tolerates_a_supervisor_that_exited_before_close():
    """A crashed supervisor is disconnect evidence, not a cleanup failure.

    The acknowledge/close recovery paths must be able to dispose of an
    app-server that already died with a non-zero status; only a supervisor
    that exits non-zero while this close was underway stays an error.
    """
    from sico.local_process import LocalProcess

    def build(poll_value, returncode):
        process = object.__new__(LocalProcess)
        process._lock = threading.Lock()
        process._control = -1
        process.child = SimpleNamespace(
            stdin=SimpleNamespace(close=lambda: None),
            poll=lambda: poll_value,
            wait=lambda timeout=None: returncode,
            returncode=returncode,
        )
        return process

    build(137, 137).close()
    with pytest.raises(RuntimeError):
        build(None, 137).close()


@pytest.mark.parametrize("body,code", [
    ("import definitely_missing_app_server_module", "import_failed"),
    ("import sys; sys.stderr.write('401 Unauthorized token=secret-sentinel'); sys.exit(2)",
     "auth_failed"),
])
def test_startup_failure_is_classified_and_logged_privately(tmp_path, body, code):
    log = DiagnosticLog(
        tmp_path / "rpc.jsonl", component="rpc-test",
        secrets_=("secret-sentinel",),
    )
    with pytest.raises(rpc.RpcError) as caught:
        rpc.AppServer(
            [sys.executable, "-s", "-c", body], {}, tmp_path, diagnostic=log)
    assert caught.value.code == code
    assert caught.value.diagnostic_id == log.correlation_id
    text = log.path.read_text()
    assert code in text
    assert "secret-sentinel" not in text
    assert body not in text
