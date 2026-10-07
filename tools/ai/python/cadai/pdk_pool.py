"""Bounded per-collection queues sharing a host/user-wide 32-process budget."""
from __future__ import annotations

import fcntl
from sicolock import lock as state_lock
import atexit
from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path
import queue
import stat
import threading
import time
import uuid
import weakref

from .pdk_schema import PdkUnavailable
from .pdk_worker import DbAccessWorker, private_directory, settings, stable_device, configuration
from .process_monitor import current_session, process_scope

MAX_PROCESSES = 32
_cancellation = ContextVar("pdk_collection_cancellation", default=None)
_pools = weakref.WeakSet()


@contextmanager
def collection_scope(cancelled):
    """Capture the owning task's cancellation, including time between tool calls."""
    token = _cancellation.set(cancelled)
    try:
        yield
    finally:
        _cancellation.reset(token)


def check_cancelled():
    callback = _cancellation.get()
    if callback and callback():
        raise PdkUnavailable("collection_cancelled", "PDK collection cancelled")


class PoolCancellation:
    def __init__(self, callback, timeout):
        self.event = threading.Event()
        self.callback = callback
        self.timeout = timeout
        self.touch()

    def touch(self):
        self.deadline = time.monotonic() + self.timeout

    def set(self):
        self.event.set()

    def is_set(self):
        try:
            expired = time.monotonic() >= self.deadline or (self.callback and self.callback())
        except Exception:
            expired = True  # An unavailable owner cannot authorize more work.
        if expired:
            self.set()
        return self.event.is_set()

    def wait(self, timeout):
        deadline = time.monotonic() + timeout
        while not self.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self.event.wait(min(0.05, remaining))
        return True


def _shutdown():
    pools = tuple(_pools)
    for pool in pools:
        pool.cancelled.set()
    for pool in pools:
        pool.close()


atexit.register(_shutdown)


def claim_slot(cancel, timeout):
    """flock survives parent death in the child; stale lock files are harmless."""
    root = Path("/tmp") / ("sico-pdk-slots-" + str(os.getuid()))
    root.mkdir(mode=0o700, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PdkUnavailable("pdk_worker_slots_unavailable", "PDK process budget directory is not private")
    deadline = time.monotonic() + timeout
    while not cancel.is_set():
        for index in range(MAX_PROCESSES):
            fd = os.open(root / str(index), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise PdkUnavailable("pdk_worker_slots_unavailable", "Invalid PDK process budget file")
                state_lock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                continue
            except BaseException:
                os.close(fd)
                raise
            return fd
        if time.monotonic() >= deadline:
            raise PdkUnavailable("pdk_worker_queue_timeout", "Timed out waiting for a PDK process slot")
        cancel.wait(0.05)
    raise PdkUnavailable("collection_cancelled", "PDK collection cancelled")


class PdkPool:
    def __init__(self, root, environment, origin, library, rows, probe, *, worker_factory=DbAccessWorker):
        self.root = Path(root) / ("run-" + uuid.uuid4().hex)
        private_directory(self.root)
        self.events = queue.Queue(maxsize=64)
        self.pending = queue.Queue()
        self.threads = []
        self.config = settings(environment, origin)
        self.configuration = configuration(self.config, environment)
        self.cancelled = PoolCancellation(_cancellation.get(), max(180, self.config[1] * 2))
        self.worker_limit = min(self.config[0], len(rows))
        self.active = 0
        self.started = 0
        self.lock = threading.Lock()
        self.error = None
        self.environment, self.origin, self.library = dict(environment), origin, library
        self.probe, self.factory = probe, worker_factory
        self.process_session = current_session()
        self.reference = stable_device(probe)
        for row in rows:
            self.pending.put(row)
        _pools.add(self)

    def start(self):
        if self.threads:
            raise RuntimeError("PDK pool was already started")
        for index in range(self.worker_limit):
            thread = threading.Thread(target=self._run, args=(index,), name="pdk-dbaccess", daemon=True)
            self.threads.append(thread)
            thread.start()

    def _emit(self, event):
        while not self.cancelled.is_set():
            try:
                self.events.put(event, timeout=0.05)
                return
            except queue.Full:
                continue

    def _run(self, index):
        with process_scope(self.process_session):
            self._run_scoped(index)

    def _run_scoped(self, index):
        worker = None
        slot = None
        try:
            slot = claim_slot(self.cancelled, self.config[1])
            worker = self.factory(self.root / str(index), self.environment, self.origin,
                                  self.library, self.cancelled, self.config, slot)
            with self.lock:
                self.active += 1
                self.started += 1
            observed = worker.capture(self.probe["data"]["identity"]["target"])
            if stable_device(observed) != self.reference:
                raise PdkUnavailable("pdk_worker_context_mismatch", "Background effective CDF or symbol differs from the live PDK probe")
            while not self.cancelled.is_set():
                try:
                    row = self.pending.get_nowait()
                except queue.Empty:
                    break
                cell = row["identity"]["target"]["cell"]
                self._emit(("running", cell, None))
                value = worker.capture(row["identity"]["target"])
                self._emit(("done", cell, value))
        except Exception as exc:
            if not self.cancelled.is_set():
                self._fail(exc)
        finally:
            try:
                if worker is not None:
                    worker.close()
            except Exception as exc:
                self._fail(exc)
            finally:
                if worker is not None:
                    with self.lock:
                        self.active -= 1
                if slot is not None:
                    os.close(slot)

    def _fail(self, error):
        with self.lock:
            if self.error is None:
                self.error = error
        self.cancelled.set()

    def check(self):
        if self.error is not None:
            raise self.error
        if self.cancelled.is_set():
            raise PdkUnavailable("collection_cancelled", "PDK collection cancelled or its owner stopped advancing")

    def drain(self, *, seconds=1.0, limit=32):
        """A bounded MCP turn; workers retain their PDK initialization between turns."""
        deadline = time.monotonic() + seconds
        self.check()
        self.cancelled.touch()
        events = []
        while len(events) < limit:
            self.check()
            try:
                event = self.events.get(timeout=max(0, min(0.05, deadline - time.monotonic())))
            except queue.Empty:
                if time.monotonic() >= deadline or not any(t.is_alive() for t in self.threads):
                    break
                continue
            events.append(event)
            if event[0] == "failed":
                break
            if time.monotonic() >= deadline:
                break
        return events

    def close(self):
        self.cancelled.set()
        deadline = time.monotonic() + 5
        for thread in self.threads:
            thread.join(timeout=max(0, deadline - time.monotonic()))
        if any(t.is_alive() for t in self.threads):
            raise PdkUnavailable("pdk_worker_shutdown_failed", "A PDK worker did not stop")
        _pools.discard(self)
