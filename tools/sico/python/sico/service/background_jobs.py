"""Own background execution jobs and their retained evidence."""
from __future__ import annotations

import fcntl
from sicolock import lock as state_lock
import json
import math
import os
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from ..core.contracts import BoundContext, identifier
from ..storage.journal import open_private, private_dir
from ..transport.framing import ProtocolError, strict_json
from .background_capability import build_background_probe
from .background_environment import capture_environment
from .background_retained import pending, scan
from .background_worker import (
    _read,
    _write,
)
from .execution_backend import ExecutionRequest, backend_kind, validate_progress
from .local_execution import LocalExecutionBackend

PROTOCOL = 'cad_ai_background_jobs.v1'
TERMINAL = {'completed', 'cancelled', 'running_unknown', 'background_unavailable',
            'worker_timeout', 'cleanup_failed', 'interrupted', 'result_invalid', 'queue_timeout'}


def copy(value):
    return json.loads(json.dumps(value, allow_nan=False))


def load_background_runtime(path):
    """Explicit site configuration only; no search of project cds.lib or PDKs."""
    if not path:
        return None
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('Background configuration must be absolute')
    from .background_config import load_settings

    return load_settings(path)[0]


class BackgroundJobs:
    def __init__(self, root, runtime, *, concurrency=1, queue_limit=32, queue_timeout=300,
                 worker_options=None,
                 backends=None, execution_backend="local", environment=None):
        if type(concurrency) is not int or not 1 <= concurrency <= 4:
            raise ValueError('Invalid worker concurrency')
        if type(queue_limit) is not int or not 1 <= queue_limit <= 128:
            raise ValueError('Invalid background queue limit')
        if isinstance(queue_timeout, bool) or not math.isfinite(queue_timeout) or not 0 < queue_timeout <= 3600:
            raise ValueError('Invalid background queue deadline')
        self.root = Path(root).absolute()
        private_dir(self.root)
        self.worker_root = self.root / 'workers'
        self.records = self.root / 'records'
        private_dir(self.worker_root)
        private_dir(self.records)
        self._unresolved = scan(self.records)
        self.service_id = uuid.uuid4().hex
        self.leases = self.root / 'services'
        private_dir(self.leases)
        self._backends = dict(backends or {})
        self._backends.setdefault("local", LocalExecutionBackend(
            self.root, runtime, worker_options=worker_options))
        from .lsf_execution import LsfExecutionBackend

        self._backends.setdefault("lsf", LsfExecutionBackend(self.root))
        self.environment = capture_environment(environment)
        self._backend_kind = backend_kind(dict(execution_backend=execution_backend))
        if any(key != backend.kind for key, backend in self._backends.items()):
            raise ValueError("Execution backend registration mismatch")
        self._lease = open_private(self.leases / self.service_id, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        state_lock(self._lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.queue_limit = queue_limit
        self.queue_timeout = queue_timeout
        self._queue_deadlines = {}
        self._changed = threading.Condition(threading.RLock())
        self._queue, self._rows, self._workers = deque(), {}, {}
        self._active = set()
        self._busy = threading.Event()
        self._closing = False
        self._persistence_error = False
        self._threads = [threading.Thread(target=self._run, name='background-jobs', daemon=True)
                         for _ in range(concurrency)]
        self._threads.append(threading.Thread(target=self._expire_queue, name='background-queue', daemon=True))
        for thread in self._threads:
            thread.start()

    def _backend(self, kind):
        backend = self._backends.get(kind)
        if backend is None:
            raise ValueError("Captured execution backend is unavailable: " + kind)
        return backend

    def _save(self, row):
        try:
            _write(self.records / (row['job_id'] + '.json'), row)
        except (OSError, ValueError):
            self._persistence_error = True
            raise
        self._rows[row['job_id']] = row
        if pending(row):
            self._unresolved.add(row['job_id'])
        else:
            self._unresolved.discard(row['job_id'])
        self._changed.notify_all()

    def _owner_active(self, row):
        service_id = row.get('service_id', '')
        if len(service_id) != 32 or any(c not in '0123456789abcdef' for c in service_id):
            return False
        try:
            fd = open_private(self.leases / service_id, os.O_RDONLY)
        except FileNotFoundError:
            return False
        try:
            try:
                state_lock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            return False
        finally:
            os.close(fd)

    def submit_background(self, method, params, *, session_id, origin,
                          environment=None, operation_id=None, backend=None):
        with self._changed:
            return self._submit_background(method, params, session_id=session_id, origin=origin,
                environment=environment, operation_id=operation_id, backend=backend)

    def _submit_background(self, method, params, *, session_id, origin, environment,
                           operation_id, backend):
        identifier(session_id)
        if operation_id is not None:
            from .service_protocol import require_id

            require_id(operation_id)
            path = self.records / (operation_id + '.json')
            if path.exists():
                previous = self._get(operation_id, session_id)
                if (previous['method'] != method or previous['params'] != params
                        or previous['origin'] != origin.record()):
                    raise ProtocolError('Background operation ID conflict')
                return copy(previous)
        params = copy(params)
        build_background_probe(method, params)
        origin = BoundContext.from_record(origin.record())
        target = {k: params.get(k, 'schematic') for k in ('lib', 'cell', 'view')}
        if origin.snapshot.get('cellview') != target:
            raise ValueError('Background target does not match captured design')
        backend = backend or self._backend(self._backend_kind)
        if not backend.available:
            raise ValueError('background_unavailable: 未配置后台运行环境')
        with self._changed:
            if self._closing:
                raise ValueError('Background service is closing')
            if self._persistence_error:
                raise ValueError('Background persistence failed; restart service after repair')
            if len(self._queue) >= self.queue_limit:
                raise ValueError('Background queue is full')
            request = ExecutionRequest(operation_id or uuid.uuid4().hex,
                                       uuid.uuid4().hex, session_id,
                                       origin, method, params,
                                       self.environment if environment is None else environment)
            worker = backend.create(request, self._progress)
            row = dict(protocol=PROTOCOL, job_id=request.job_id, worker_id=request.worker_id,
                       service_id=self.service_id, execution_backend=backend.kind,
                       owner_session_id=session_id, origin=origin.record(), method=method,
                       params=params, target=target, state='accepted', accepted_at=time.time(),
                       automatic_resume_allowed=False, execution_mode='background')
            accepted = copy(row)
            # Persist the schedulable state before acknowledgement or dispatch.
            self._save(dict(row, state='queued', queued_at=time.time(),
                            queue_deadline_at=time.time() + self.queue_timeout))
            self._workers[request.job_id] = worker
            self._queue.append(request.job_id)
            self._queue_deadlines[request.job_id] = time.monotonic() + self.queue_timeout
            self._busy.set()
            self._changed.notify_all()
            return accepted

    def _get(self, job_id, session_id):
        if not isinstance(job_id, str) or len(job_id) != 32 or any(c not in '0123456789abcdef' for c in job_id):
            raise ValueError('Invalid background job ID')
        row = self._rows.get(job_id)
        if row is None:
            row = strict_json(_read(self.records / (job_id + '.json')))
            if row.get('protocol') != PROTOCOL or row.get('job_id') != job_id:
                raise ValueError('Background record identity mismatch')
            if row['owner_session_id'] != session_id:
                raise ValueError('Background job owner mismatch')
            active = self._owner_active(row)
            if not active and (row['state'] not in {'cancelled', 'background_unavailable', 'queue_timeout'}
                               or (self.worker_root / job_id).exists()):
                row = self._backend(backend_kind(row)).recover(row)
                if backend_kind(row) == 'lsf':
                    self._save(row)
        else:
            active = ((not self._closing or bool(self._active))
                      and row['service_id'] == self.service_id)
            if not active and row['service_id'] != self.service_id:
                active = self._owner_active(row)
            if (backend_kind(row) == 'lsf' and job_id not in self._workers and pending(row)
                    and (not active or row['service_id'] == self.service_id)):
                row = self._backend('lsf').recover(row)
                self._save(row)
        if row['owner_session_id'] != session_id:
            raise ValueError('Background job owner mismatch')
        return dict(row, owner_service_active=active,
                    control_available=job_id in self._workers or ((not active or
                        row.get("service_id") == self.service_id)
                        and backend_kind(row) == "lsf" and pending(row)))

    def get_background_status(self, job_id, *, session_id):
        with self._changed:
            return copy(self._get(job_id, session_id))

    def list_background(self, *, session_id):
        identifier(session_id)
        with self._changed:
            keys = set(self._rows)
            keys.update(p.stem for p in self.records.glob('*.json'))
            rows = []
            for key in sorted(keys):
                try:
                    rows.append(copy(self._get(key, session_id)))
                except ProtocolError:
                    raise
                except (ValueError, OSError, KeyError):
                    continue
            return sorted(rows, key=lambda row: row['accepted_at'], reverse=True)[:200]

    def cancel_background(self, job_id, *, session_id):
        with self._changed:
            row = self._get(job_id, session_id)
            if backend_kind(row) == 'lsf' and job_id not in self._workers and pending(row):
                if row['owner_service_active'] and row['service_id'] != self.service_id:
                    raise ValueError('Cancel this job in its owning desktop')
                result = self._backend('lsf').cancel_retained(row)
                self._save(result)
                return copy(result)
            if row['state'] in TERMINAL and not (backend_kind(row) == 'lsf' and pending(row)):
                return copy(row)
            if row['state'] == 'recovering':
                return copy(row)
            if job_id not in self._workers:
                raise ValueError('Cancel this job in its owning desktop')
            if job_id in self._queue:
                self._queue.remove(job_id)
                self._queue_deadlines.pop(job_id, None)
                self._workers.pop(job_id)
                row = dict(row, state='cancelled', finished_at=time.time(),
                           operation_dispatched=False)
            else:
                self._workers[job_id].cancel()
                row = dict(row, state='running_unknown' if row.get('request_id') else 'cancelling',
                           cancel_requested_at=time.time())
            try:
                self._save(row)
            finally:
                if not self._queue and not self._active:
                    self._busy.clear()
                self._changed.notify_all()
            return copy(row)

    def _progress(self, receipt):
        with self._changed:
            row = self._rows[receipt['job_id']]
            validate_progress(row, receipt)
            state = receipt['state']
            if row.get('cancel_requested_at') and not receipt.get('finished_at'):
                state = 'running_unknown' if receipt.get('request_id') else 'cancelling'
            self._save({**row, **receipt, 'protocol': PROTOCOL, 'state': state})

    def _failed(self, key, exc):
        row = dict(self._rows[key], state='interrupted', finished_at=time.time(),
                   reason='persistence_failed' if self._persistence_error else 'worker_service_failed',
                   error_type=type(exc).__name__)
        try:
            self._save(row)
        except (OSError, ValueError):
            # An in-memory failure is never advertised as a durable receipt.
            self._rows[key] = dict(row, receipt_persisted=False)

    def _run(self):
        while True:
            with self._changed:
                self._changed.wait_for(lambda: self._closing or (self._queue
                    and not self._unresolved.difference(self._workers)))
                if self._closing and not self._queue:
                    return
                key = self._queue.popleft()
                deadline = self._queue_deadlines.pop(key)
                if time.monotonic() >= deadline:
                    self._queue_expired(key)
                    continue
                self._active.add(key)
                worker = self._workers[key]
            try:
                if self._persistence_error:
                    raise OSError('Background persistence failed')
                worker.run()
            except Exception as exc:
                with self._changed:
                    self._failed(key, exc)
            finally:
                with self._changed:
                    self._active.discard(key)
                    self._workers.pop(key, None)
                    if not self._queue and not self._active:
                        self._busy.clear()
                    self._changed.notify_all()

    def _queue_expired(self, key):
        self._workers.pop(key, None)
        try:
            self._save(dict(self._rows[key], state='queue_timeout', finished_at=time.time(),
                            operation_dispatched=False, reason='queue_timeout'))
        except (OSError, ValueError) as exc:
            self._failed(key, exc)
        if not self._queue and not self._active:
            self._busy.clear()

    def _expire_queue(self):
        with self._changed:
            while not self._closing:
                now = time.monotonic()
                for key in tuple(self._queue):
                    if now >= self._queue_deadlines[key]:
                        self._queue.remove(key)
                        self._queue_deadlines.pop(key)
                        self._queue_expired(key)
                self._changed.wait(.05)

    def read_background_result(self, job_id, artifact_ref, *, session_id):
        with self._changed:
            row = copy(self._get(job_id, session_id))
        if row['state'] != 'completed' or not artifact_ref or artifact_ref != row.get('artifact'):
            raise ValueError('Background result reference is unavailable or stale')
        result = self._backend(backend_kind(row)).read_result(row)
        for key in ('job_id', 'worker_id', 'request_id', 'instance_id', 'generation', 'bridge_id', 'router_id'):
            if result[key] != row[key]:
                raise ValueError('Background result identity mismatch')
        return dict(result, artifact=copy(artifact_ref), owner_session_id=session_id,
                    origin=row['origin'], automatic_resume_allowed=False)

    @property
    def busy(self):
        # Queried by Qt; never contend with fsync/record scans under _changed.
        return self._busy.is_set() or bool(self._unresolved)

    def close(self):
        with self._changed:
            if self._closing:
                return
            self._closing = True
            for key in tuple(self._queue) + tuple(self._active):
                try:
                    worker = self._workers[key]
                    if backend_kind(self._rows[key]) == 'lsf' and key in self._active:
                        worker.detach()  # Scheduler cancellation requires an explicit command.
                    else:
                        self.cancel_background(
                            key, session_id=self._rows[key]['owner_session_id'])
                except (OSError, ValueError) as exc:
                    self._failed(key, exc)
            self._changed.notify_all()

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        for thread in self._threads:
            thread.join(None if deadline is None else max(0, deadline - time.monotonic()))
        stopped = all(not thread.is_alive() for thread in self._threads)
        if stopped:
            with self._changed:
                if self._lease >= 0:
                    os.close(self._lease)
                    self._lease = -1
        return stopped
