"""Gateway session reuse, worker lifecycle and cancellation boundaries."""

from __future__ import annotations

import io
import threading
import time
from types import SimpleNamespace

import pytest
from sico.codex import gateway_session


class Process:
    def __init__(self, alive=True):
        self.child = SimpleNamespace(
            stdout=io.BytesIO(), stdin=io.BytesIO(), poll=lambda: None if alive else 1)


class Job:
    def __init__(self, alive=True):
        self.process = Process(alive)
        self.record = {}
        self.saved = 0
        self.closed = 0

    def save(self):
        self.saved += 1

    def close(self):
        self.closed += 1


def gateway():
    return SimpleNamespace(
        environment={}, cwd="/work", evidence={}, start_timeout=1,
        stopped=threading.Event(), cleanup_failed=False)


def test_reuse_and_time_environment_are_bounded():
    assert gateway_session.reuse_enabled({}, False) is True
    assert gateway_session.reuse_enabled({"SICO_CODEX_LSF_REUSE": "0"}, False) is False
    assert gateway_session.reuse_enabled({"SICO_CODEX_LSF_REUSE": "1"}, True) is False
    with pytest.raises(ValueError):
        gateway_session.reuse_enabled({"SICO_CODEX_LSF_REUSE": "yes"}, False)
    assert gateway_session.session_idle({}) == 600
    assert gateway_session.max_age({}) == 14 * 86400
    assert gateway_session.session_idle({"SICO_CODEX_LSF_SESSION_IDLE": "2"}) == 2
    with pytest.raises(ValueError):
        gateway_session.session_idle({"SICO_CODEX_LSF_SESSION_IDLE": "0"})
    with pytest.raises(ValueError):
        gateway_session.max_age({"SICO_CODEX_LSF_MAX_AGE": "9999999"})


def test_note_job_updates_only_recorded_jobs():
    job = Job()
    gateway_session._note_job(job, requests=2)
    assert job.record == {"requests": 2} and job.saved == 1
    gateway_session._note_job(SimpleNamespace(), requests=2)


def test_connection_sends_and_closes_owned_job():
    job = Job()
    value = gateway_session.Connection(job)
    value.send(gateway_session.encode("x"))
    assert job.process.child.stdin.getvalue().startswith(b"SICO-GATEWAY/1 ")
    assert value.alive() is True
    value.close()
    assert job.closed == 1


def test_open_connection_ready_and_errors(monkeypatch):
    class Reader:
        def __init__(self, _stream):
            self.value = {"kind": "ready"}

        def next(self, *_args):
            return self.value

    monkeypatch.setattr(gateway_session, "Frames", Reader)
    monkeypatch.setattr(gateway_session, "Connection", lambda job: SimpleNamespace(
        job=job, reader=Reader(None)))
    value = gateway_session.open_connection(gateway(), lambda *_args: Job())
    assert value.job.closed == 0

    class ErrorReader(Reader):
        def next(self, *_args):
            return {"kind": "error", "message": "bad worker"}
    monkeypatch.setattr(gateway_session, "Connection", lambda job: SimpleNamespace(
        job=job, reader=ErrorReader(None)))
    with pytest.raises(gateway_session.WorkerError, match="bad worker"):
        gateway_session.open_connection(gateway(), lambda *_args: Job())

    class WrongReader(Reader):
        def next(self, *_args):
            return {"kind": "other"}
    monkeypatch.setattr(gateway_session, "Connection", lambda job: SimpleNamespace(
        job=job, reader=WrongReader(None)))
    with pytest.raises(gateway_session.WorkerError, match="ready"):
        gateway_session.open_connection(gateway(), lambda *_args: Job())


def test_temporary_worker_acquires_and_closes():
    connection = SimpleNamespace(close=lambda: None, job=Job())
    value = gateway_session.TemporaryWorker(gateway(), lambda *_args: Job())
    value.acquire = lambda: connection
    assert value.acquire() is connection
    value.connection = connection
    value.close()
    assert value.connection is None


def test_session_worker_busy_reuses_and_discards_after_age(monkeypatch):
    connections = []

    def open_(_gateway, _factory):
        value = SimpleNamespace(
            job=Job(), alive=lambda: True, close=lambda: connections.append("close"),
            send=lambda _frame: connections.append("send"))
        connections.append(value)
        return value

    monkeypatch.setattr(gateway_session, "open_connection", open_)
    value = gateway_session.SessionWorker(
        gateway(), lambda *_args: Job(), idle_timeout=100, max_age=1000)
    value._ensure()
    first = value._ensure()
    assert first is value.connection
    assert value.request_lock.acquire(blocking=False)
    with pytest.raises(gateway_session.Busy):
        value.acquire()
    value.request_lock.release()
    value.cancel()
    assert "send" in connections and value.stats["cancel_restarts"] == 1
    assert value.acquire() is value.connection
    value.release(healthy=False)
    assert value.connection is None
    value.close()


def test_session_worker_recycles_old_generation(monkeypatch):
    monkeypatch.setattr(gateway_session, "open_connection",
                        lambda *_args: SimpleNamespace(
                            job=Job(), alive=lambda: True,
                            close=lambda: None, send=lambda _frame: None))
    value = gateway_session.SessionWorker(
        gateway(), lambda *_args: Job(), idle_timeout=100, max_age=0)
    value._ensure()
    value.started_at = time.monotonic() - 1
    value.acquire()
    assert value.stats["recycles"] == 1
    value.close()


def test_prewarm_failure_and_closed_worker(monkeypatch):
    monkeypatch.setattr(gateway_session, "open_connection",
                        lambda *_args: (_ for _ in ()).throw(RuntimeError("bad")))
    value = gateway_session.SessionWorker(
        gateway(), lambda *_args: Job(), idle_timeout=1, max_age=10)
    value._prewarm()
    assert value.stats["worker_failures"] == 1
    value.close()
