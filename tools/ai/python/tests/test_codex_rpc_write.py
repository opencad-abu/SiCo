"""Deadlines/cancellation cover the entire real pipe transaction without replay."""

import json
import os
import queue
import select
import threading
import time

import pytest
from codex_rpc_fixture import pipes  # noqa: F401

from sico.codex.rpc import RpcError, RpcRejected
from sico.codex import rpc_write

LARGE = {'payload': 'x' * 1048576}


def observed(operation, errors):
    try:
        operation()
    except Exception as exc:
        errors.append(exc)


def wait_readable(descriptor):
    assert select.select([descriptor], [], [], 1)[0]


def test_live_peer_not_reading_has_send_deadline(pipes):
    rpc, peer, _, _ = pipes
    started = time.monotonic()
    with pytest.raises(RpcError, match='write timed out') as failure:
        rpc.request('probe', LARGE, timeout=.05)
    assert time.monotonic() - started < .5
    assert failure.value.phase == 'write'
    assert 0 < failure.value.sent_bytes < len(LARGE['payload'])
    assert failure.value.code == 'timeout'
    assert failure.value.diagnostic_id == rpc.diagnostic_id
    assert rpc.stopped.is_set()
    raw = os.read(peer, 1048576)
    assert len(raw) == failure.value.sent_bytes and b'\n' not in raw
    with pytest.raises(RpcError):
        rpc.send({'method': 'must-not-replay'})
    assert not select.select([peer], [], [], 0)[0]


@pytest.mark.parametrize('slot', ['writer', 'request_slot'])
def test_waiting_for_ownership_uses_same_deadline(pipes, slot):
    rpc, peer, _, _ = pipes
    lock = rpc._writer._lock if slot == 'writer' else rpc._request_lock
    lock.acquire()
    try:
        started = time.monotonic()
        with pytest.raises(RpcError) as failure:
            rpc.request('probe', {}, timeout=.05)
        assert time.monotonic() - started < .5
        assert failure.value.phase == slot and failure.value.sent_bytes == 0
        assert not rpc.stopped.is_set()
        assert not select.select([peer], [], [], 0)[0]
    finally:
        lock.release()


def test_cancel_before_write_sends_nothing(pipes):
    rpc, peer, _, _ = pipes
    with pytest.raises(RpcError, match='cancelled') as failure:
        rpc.send({'method': 'probe'}, cancelled=lambda: True)
    assert failure.value.sent_bytes == 0 and not rpc.stopped.is_set()
    assert failure.value.code == 'cancelled'
    assert not select.select([peer], [], [], 0)[0]


@pytest.mark.parametrize('closing', [False, True])
def test_cancel_or_close_interrupts_write_with_full_receive_queue(pipes, closing):
    rpc, peer, child, start = pipes
    rpc.messages = queue.Queue(maxsize=1)
    rpc.messages.put({'method': 'occupy'})
    os.write(child, b'{"method":"queued"}\n')
    cancel, errors = threading.Event(), []
    worker = start(lambda: observed(lambda: rpc.send(LARGE, cancelled=cancel.is_set), errors))
    wait_readable(peer)
    started = time.monotonic()
    cancel.set()
    if closing:
        rpc.close()
    worker.join(.5)
    assert not worker.is_alive() and time.monotonic() - started < .6
    assert len(errors) == 1 and isinstance(errors[0], RpcError)
    assert errors[0].sent_bytes > 0 and rpc.stopped.is_set()


def test_response_wait_uses_remaining_write_budget(pipes):
    rpc, peer, _, start = pipes
    drained = threading.Event()

    def drain():
        wait_readable(peer)
        time.sleep(.15)
        raw = b''
        while not raw.endswith(b'\n'):
            raw += os.read(peer, 65536)
        drained.set()

    start(drain)
    started = time.monotonic()
    with pytest.raises(RpcError, match='response timed out') as failure:
        rpc.request('probe', LARGE, timeout=.25)
    elapsed = time.monotonic() - started
    assert drained.is_set() and .23 < elapsed < .36
    assert failure.value.sent_bytes > 1048576 and rpc.stopped.is_set()


def test_cancel_during_response_stops_connection(pipes):
    rpc, peer, _, start = pipes
    cancel, errors = threading.Event(), []
    rpc.cancelled = cancel.is_set
    worker = start(lambda: observed(lambda: rpc.request('probe', {}), errors))
    wait_readable(peer)
    cancel.set()
    worker.join(.5)
    assert not worker.is_alive()
    assert errors[0].phase == 'response' and errors[0].sent_bytes > 0
    assert rpc.stopped.is_set()


def test_partial_writes_eintr_and_concurrent_frames_do_not_interleave(pipes, monkeypatch):
    rpc, peer, _, start = pipes
    original = os.write
    interrupted = False

    def small_write(fd, raw):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise InterruptedError()
        return original(fd, raw[:997])

    monkeypatch.setattr(rpc_write.os, 'write', small_write)
    received, errors = [], []

    def drain():
        chunks, count = [], 0
        while count < 2:
            chunk = os.read(peer, 8192)
            if not chunk:
                return
            chunks.append(chunk)
            count += chunk.count(b'\n')
        received.extend(json.loads(line) for line in b''.join(chunks).splitlines())

    reader = start(drain)
    workers = [start(lambda value=value: observed(
        lambda: rpc.send({'value': value, **LARGE}, timeout=2), errors)) for value in [1, 2]]
    for worker in workers + [reader]:
        worker.join(3)
        assert not worker.is_alive()
    assert not errors and sorted(row['value'] for row in received) == [1, 2]
    assert all(row['payload'] == LARGE['payload'] for row in received)
    assert not rpc.stopped.is_set()


def test_correlated_rejection_preserves_connection_and_queued_final_response(pipes):
    rpc, peer, child, start = pipes

    def server():
        first = json.loads(os.read(peer, 65536))
        os.write(child, json.dumps({'id': first['id'], 'error': {'code': -1}}).encode() + b'\n')
        second = json.loads(os.read(peer, 65536))
        os.write(child, json.dumps({'id': second['id'], 'result': {'ok': True}}).encode() + b'\n')

    start(server)
    with pytest.raises(RpcRejected):
        rpc.request('reject', {})
    assert not rpc.stopped.is_set()
    assert rpc.request('accept', {}) == {'ok': True}


def test_auth_rejection_is_classified_without_copying_upstream_body(pipes):
    rpc, peer, child, start = pipes
    secret = "upstream-secret-sentinel"

    def server():
        first = json.loads(os.read(peer, 65536))
        os.write(child, json.dumps({
            "id": first["id"],
            "error": {"code": 401, "message": "401 Unauthorized " + secret},
        }).encode() + b"\n")

    start(server)
    with pytest.raises(RpcRejected) as caught:
        rpc.request("reject-auth", {})
    assert caught.value.code == "auth_failed"
    assert caught.value.diagnostic_id == rpc.diagnostic_id
    text = rpc._diagnostic.path.read_text()
    assert secret not in text
    assert "auth_failed" in text


@pytest.mark.parametrize('timeout', [-1, float('nan'), float('inf'), True, '1'])
def test_invalid_deadline_never_writes(pipes, timeout):
    rpc, peer, _, _ = pipes
    with pytest.raises(ValueError):
        rpc.request('probe', {}, timeout=timeout)
    assert not select.select([peer], [], [], 0)[0]
