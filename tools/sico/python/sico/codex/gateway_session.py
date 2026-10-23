"""Session-scoped LSF worker lifetime; one job serves many HTTP requests."""

from __future__ import annotations

import threading
import time

from .gateway_job import GatewayJob
from .gateway_wire import Frames, encode, send

DEFAULT_SESSION_IDLE = 600
DEFAULT_MAX_AGE = 14 * 86400
REUSE_ENV = "SICO_CODEX_LSF_REUSE"
SESSION_IDLE_ENV = "SICO_CODEX_LSF_SESSION_IDLE"
MAX_AGE_ENV = "SICO_CODEX_LSF_MAX_AGE"


class Busy(RuntimeError):
    """The resident worker is already serving another request."""


class WorkerError(RuntimeError):
    """The worker did not become ready or its protocol failed."""


class Connection:
    """One owned worker job with serialized frame writes."""

    def __init__(self, job):
        self.job = job
        self.reader = Frames(job.process.child.stdout)
        self.output = job.process.child.stdin
        self.send_lock = threading.Lock()

    def send(self, frame):
        with self.send_lock:
            send(self.output, frame)

    def alive(self):
        return self.job.process.child.poll() is None

    def close(self):
        self.job.close()
        try:
            self.job.process.child.stdout.close()
        except OSError:
            pass


def _note_job(job, **values):
    record = getattr(job, "record", None)
    if isinstance(record, dict):
        record.update(values)
        save = getattr(job, "save", None)
        if callable(save):
            save()


def reuse_enabled(environment, auxiliary):
    if auxiliary:
        return False
    raw = str(environment.get(REUSE_ENV, "")).strip()
    if raw in ("", "1"):
        return True
    if raw == "0":
        return False
    raise ValueError(REUSE_ENV + " must be unset, 0 or 1")


def _seconds(environment, name, default, maximum):
    raw = str(environment.get(name, "")).strip()
    if not raw:
        return default
    if not raw.isdigit() or not 0 < int(raw) <= maximum:
        raise ValueError(name + " is outside the supported range")
    return int(raw)


def session_idle(environment):
    return _seconds(environment, SESSION_IDLE_ENV, DEFAULT_SESSION_IDLE, 86400)


def max_age(environment):
    return _seconds(environment, MAX_AGE_ENV, DEFAULT_MAX_AGE, 15 * 86400)


def open_connection(gateway, job_factory=GatewayJob):
    job = job_factory(gateway.environment, gateway.cwd, gateway.evidence)
    try:
        connection = Connection(job)
        first = connection.reader.next(
            gateway.start_timeout, lambda: gateway.stopped.is_set()
        )
        if first.get("kind") == "error":
            raise WorkerError(
                str(first.get("message", "LSF AI worker failed before readiness"))
            )
        if first.get("kind") != "ready":
            raise WorkerError("Worker did not become ready")
        _note_job(job, requests=0)
        return connection
    except BaseException:
        try:
            job.close()
        except (OSError, RuntimeError, TimeoutError):
            pass
        raise


class TemporaryWorker:
    """One request-owned job for concurrency fallback or auxiliary sessions."""

    def __init__(self, gateway, job_factory=GatewayJob):
        self.gateway, self.job_factory = gateway, job_factory
        self.connection = None

    def acquire(self):
        self.connection = open_connection(self.gateway, self.job_factory)
        _note_job(self.connection.job, requests=1)
        return self.connection

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None


class SessionWorker:
    """One reusable job, with idle release and pre-kill aging."""

    def __init__(self, gateway, job_factory=GatewayJob, *, idle_timeout, max_age):
        self.gateway, self.job_factory = gateway, job_factory
        self.idle_timeout, self.max_age = idle_timeout, max_age
        self.connection = None
        self.started_at = None
        self.last_used = time.monotonic()
        self.request_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.closed = threading.Event()
        self.dirty = False
        self.stats = {
            "jobs_submitted": 0,
            "requests_served": 0,
            "idle_releases": 0,
            "recycles": 0,
            "worker_failures": 0,
            "cancel_restarts": 0,
        }
        self.monitor = threading.Thread(target=self._monitor, daemon=True)
        self.monitor.start()

    def prewarm(self):
        threading.Thread(target=self._prewarm, daemon=True).start()

    def _prewarm(self):
        try:
            self._ensure()
        except (OSError, RuntimeError, ValueError, TimeoutError):
            self.stats["worker_failures"] += 1

    def _ensure(self):
        with self.state_lock:
            if self.connection is not None and self.connection.alive():
                return self.connection
            self._discard()
            connection = open_connection(self.gateway, self.job_factory)
            if self.closed.is_set():
                try:
                    connection.close()
                except (OSError, RuntimeError, TimeoutError):
                    self.gateway.cleanup_failed = True
                raise WorkerError("Resident worker session closed")
            self.connection = connection
            self.started_at = time.monotonic()
            self.stats["jobs_submitted"] += 1
            return self.connection

    def acquire(self):
        if not self.request_lock.acquire(blocking=False):
            raise Busy("Resident worker is serving another request")
        try:
            self.last_used = time.monotonic()
            if (
                self.connection is not None
                and self.started_at is not None
                and time.monotonic() - self.started_at >= self.max_age
            ):
                self._discard()
                self.stats["recycles"] += 1
            return self._ensure()
        except BaseException:
            self.request_lock.release()
            raise

    def release(self, *, healthy):
        try:
            if not healthy:
                self.dirty = True
            if self.dirty:
                self._discard()
                self.dirty = False
            self._save_stats()
            self.last_used = time.monotonic()
        finally:
            self.request_lock.release()

    def note_request(self):
        self.stats["requests_served"] += 1

    def cancel(self):
        connection = self.connection
        if connection is not None:
            self.dirty = True
            self.stats["cancel_restarts"] += 1
            try:
                connection.send(encode("cancel"))
            except (OSError, ValueError):
                pass

    def _discard(self):
        if self.connection is not None:
            try:
                self.connection.close()
            except (OSError, RuntimeError, TimeoutError):
                self.gateway.cleanup_failed = True
            self.connection = None

    def _save_stats(self):
        if self.connection is not None:
            _note_job(
                self.connection.job,
                requests=self.stats["requests_served"],
                session=dict(self.stats),
            )

    def _monitor(self):
        interval = min(30, max(1, self.idle_timeout / 2))
        while not self.closed.wait(interval):
            if not self.request_lock.acquire(blocking=False):
                continue
            try:
                if self.connection is None:
                    continue
                if not self.state_lock.acquire(blocking=False):
                    continue
                try:
                    if self.connection is None:
                        continue
                    now = time.monotonic()
                    idle = now - self.last_used
                    age = now - (self.started_at or now)
                    if idle >= self.idle_timeout:
                        self._save_stats()
                        self._discard()
                        self.stats["idle_releases"] += 1
                    elif age >= self.max_age:
                        self._save_stats()
                        self._discard()
                        self.stats["recycles"] += 1
                finally:
                    self.state_lock.release()
            finally:
                self.request_lock.release()

    def close(self):
        self.closed.set()
        self.monitor.join(timeout=2)
        if not self.request_lock.acquire(timeout=15):
            self.gateway.cleanup_failed = True
            return
        try:
            if not self.state_lock.acquire(timeout=15):
                self.gateway.cleanup_failed = True
                return
            try:
                self._save_stats()
                self._discard()
            finally:
                self.state_lock.release()
        finally:
            self.request_lock.release()
