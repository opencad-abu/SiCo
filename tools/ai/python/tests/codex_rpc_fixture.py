"""Real bidirectional pipes for transport fault injection, with joined workers."""

import os
import queue
import threading
from collections import deque
from types import SimpleNamespace

import pytest
from sico.codex.rpc import AppServer
from sico.codex.rpc_write import PipeWriter
from sico.diagnostics import DiagnosticLog


@pytest.fixture
def pipes(tmp_path):
    incoming, child_output = os.pipe()
    peer_input, outgoing = os.pipe()
    rpc = object.__new__(AppServer)
    rpc.messages, rpc.pending = queue.Queue(maxsize=4096), deque()
    rpc.notification_handler, rpc.cancelled = None, None
    rpc.counter, rpc.exit, rpc.closed_by_client = 0, None, False
    rpc.stopped, rpc._request_lock = threading.Event(), threading.Lock()
    rpc._failure_lock, rpc._failure, rpc._closed = threading.Lock(), None, False
    rpc._diagnostic = DiagnosticLog(
        tmp_path / "rpc.jsonl", component="rpc-test")
    rpc.diagnostic_id = rpc._diagnostic.correlation_id
    rpc.child = SimpleNamespace(stdin=os.fdopen(outgoing, 'wb', buffering=0),
                                stdout=os.fdopen(incoming, 'rb', buffering=0), poll=lambda: None)
    rpc._writer = PipeWriter(rpc.child.stdin, rpc.stopped)
    rpc._process = SimpleNamespace(close=lambda: None)
    rpc.reader = threading.Thread(target=rpc._read)
    rpc.reader.start()
    workers = []

    def start(target):
        thread = threading.Thread(target=target)
        workers.append(thread)
        thread.start()
        return thread

    try:
        yield rpc, peer_input, child_output, start
    finally:
        rpc.close()
        os.close(peer_input)
        os.close(child_output)
        for worker in workers:
            worker.join(1)
            assert not worker.is_alive(), 'transport test leaked a worker'
        assert not rpc.reader.is_alive()
