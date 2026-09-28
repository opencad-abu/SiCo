"""One explicit saved-OA read per isolated Virtuoso process. No automatic replay."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shlex
import shutil
import stat
import subprocess
import threading
import time
import uuid
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path

from ..core.contracts import BoundContext, NeedsReconcile, identifier
from ..interpreter import agent_command
from ..storage.journal import open_private, private_dir, sync_directory
from ..transport.broker import ContextBroker
from ..transport.framing import strict_json
from ..transport.methods import QueryUnavailable
from ..transport.router_journal import RouterJournal
from .background_capability import MAX_PROBE_BYTES, assess_background_probe, build_background_probe
from .background_environment import capture_environment, validate_environment, worker_environment
from .background_watchdog import boot_id, process_alive, process_identity, resume_owned_guardian

PROTOCOL = 'cad_ai_background_worker.v1'
IDENTITY = ('worker_id', 'job_id', 'instance_id', 'generation', 'bridge_id',
            'router_id', 'session_id', 'target_id')


def _write(path, value):
    data = value if isinstance(value, bytes) else (json.dumps(value, ensure_ascii=False,
                                                           allow_nan=False) + '\n').encode()
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex)
    try:
        with os.fdopen(open_private(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY), 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _read(path, limit=MAX_PROBE_BYTES * 2):
    with os.fdopen(open_private(path, os.O_RDONLY), 'rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('Background record exceeds limit')
    return raw


@dataclass(frozen=True)
class BackgroundRuntime:
    virtuoso: Path
    context: Path
    context_sha256: str
    version: str
    libraries: dict
    xvfb: Path = Path('/usr/bin/Xvfb')

    def __post_init__(self):
        for field in ('virtuoso', 'context', 'xvfb'):
            object.__setattr__(self, field, Path(getattr(self, field)))
        object.__setattr__(self, 'libraries', dict(self.libraries))

    def validate(self):
        for name in ('virtuoso', 'xvfb'):
            path = Path(getattr(self, name))
            if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
                raise Unavailable(name + '_unavailable')
        path = Path(self.context)
        if not path.is_absolute() or path.suffix != '.cxt' or not path.is_file():
            raise Unavailable('context_unavailable')
        if (not re.fullmatch('[0-9a-f]{64}', self.context_sha256)
                or hashlib.sha256(path.read_bytes()).hexdigest() != self.context_sha256):
            raise Unavailable('context_checksum')
        if not isinstance(self.version, str) or not self.version.strip():
            raise Unavailable('version_required')
        if not isinstance(self.libraries, dict) or not self.libraries or len(self.libraries) > 256:
            raise Unavailable('libraries_required')
        for name, location in self.libraries.items():
            path = Path(location)
            if (not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*', name) or not path.is_absolute()
                    or not path.is_dir() or any(c in str(path) for c in '\n\r\0"$')):
                raise Unavailable('invalid_library')


class Unavailable(ValueError):
    pass


class BackgroundCancelled(RuntimeError):
    pass


class BackgroundWorker:
    """One job, with cooperative supervisor cancellation and retained evidence."""

    def __init__(self, root, runtime, *, startup_timeout=30, execution_timeout=60,
                 shutdown_timeout=5, progress=None, job_id=None, worker_id=None, environment=None):
        for value in (startup_timeout, execution_timeout, shutdown_timeout):
            if isinstance(value, bool) or not math.isfinite(value) or not 0 < value <= 3600:
                raise ValueError('Invalid worker deadline')
        self.root = Path(root).absolute()
        private_dir(self.root)
        self.runtime = runtime
        self.startup_timeout, self.execution_timeout = startup_timeout, execution_timeout
        self.shutdown_timeout = shutdown_timeout
        self.worker_id = identifier(worker_id or uuid.uuid4().hex)
        self.job_id = identifier(job_id or uuid.uuid4().hex)
        self.environment = (capture_environment() if environment is None
                            else validate_environment(environment))
        self.directory = self.root / self.job_id
        self._once = threading.Lock()
        self._used = False
        self._cancel = threading.Event()
        self._dispatched = False
        self._call_thread = None
        self.progress = progress
        self.process = self.broker = self.journal = self.guardian = None
        self.process_session = ""
        self.process_record = None
        self.receipt = dict(protocol=PROTOCOL, worker_id=self.worker_id, job_id=self.job_id,
                            instance_id='bg_' + self.worker_id, generation=uuid.uuid4().hex,
                            session_id=uuid.uuid4().hex, target_id=uuid.uuid4().hex,
                            execution_mode='background', automatic_resume_allowed=False,
                            pid=None, port=None, bridge_id=None, router_id=None, request_id=None)

    def _state(self, state, **fields):
        self.receipt.update(state=state, updated_at=time.time(), **fields)
        _write(self.directory / 'receipt.json', self.receipt)
        if self.progress:
            self.progress(json.loads(json.dumps(self.receipt)))

    def cancel(self):
        self._cancel.set()

    def _check_cancel(self):
        if self._cancel.is_set():
            raise BackgroundCancelled()

    def run(self, method, arguments):
        expression = build_background_probe(method, arguments)
        with self._once:
            if self._used:
                raise RuntimeError('A worker cannot run or replay a second job')
            self._used = True
        self.directory.mkdir(mode=0o700)
        for name in ('scratch', 'artifacts', 'logs'):
            private_dir(self.directory / name)
        self.receipt.update(method=method, target={k: arguments.get(k, 'schematic')
                                                 for k in ('lib', 'cell', 'view')},
                            created_at=time.time(), ok=False)
        self._state('starting')
        outcome, payload = 'background_unavailable', {}
        try:
            self._check_cancel()
            self.runtime.validate()
            if arguments['lib'] not in self.runtime.libraries:
                raise Unavailable('target_library_unregistered')
            self._prepare(expression)
            self._check_cancel()
            self._start()
            self._health()
            self._state('ready', health_checked_at=time.time())
            self._state('running', started_at=time.time())
            raw = self._call('assistant_call', {'method': 'eval_skill', 'code': expression},
                             self.execution_timeout)
            assessed = assess_background_probe(json.dumps(raw), target=arguments,
                                                expected_version=self.runtime.version)
            if not assessed['available']:
                raise Unavailable(assessed['reason'])
            artifact = dict(protocol=PROTOCOL, **{k: self.receipt[k] for k in IDENTITY},
                            request_id=self.receipt['request_id'], generated_at=time.time(),
                            snapshot_scope='saved_oa', evidence=raw, inspection=raw['inspection'])
            path = self.directory / 'artifacts/result.json'
            _write(path, artifact)
            data = _read(path)
            payload = dict(ok=True, snapshot_scope='saved_oa', foreground_memory_visible=False,
                           artifact=dict(path='artifacts/result.json', sha256=hashlib.sha256(data).hexdigest(),
                                         size=len(data)))
            outcome = 'completed'
        except BackgroundCancelled:
            outcome = 'running_unknown' if self._dispatched else 'cancelled'
            payload = dict(ok=False, code=outcome, reason='user_cancelled')
        except Unavailable as exc:
            payload = dict(ok=False, reason=str(exc), code='background_unavailable')
        except (TimeoutError, QueryUnavailable, NeedsReconcile) as exc:
            timed_out = isinstance(exc, TimeoutError) or getattr(exc, 'code', '') in {
                'skill_timeout', 'skill_response_pending', 'skill_reply_write_failed'}
            outcome = 'worker_timeout' if timed_out and self.receipt['state'] == 'running' else 'background_unavailable'
            reason = ('execution_timeout' if outcome == 'worker_timeout' else
                      'startup_timeout' if timed_out else 'bridge_unavailable')
            payload = dict(ok=False, reason=reason, code=outcome)
        except (OSError, ValueError, RuntimeError) as exc:
            payload = dict(ok=False, reason='worker_failed', code='background_unavailable',
                           error_type=type(exc).__name__)
        finally:
            cleanup = self._cleanup()
        report = self._watchdog_report()
        if report and report['reason'] in {'execution_timeout', 'startup_timeout'}:
            outcome = 'worker_timeout' if self._dispatched else 'background_unavailable'
            payload = dict(ok=False, code=outcome, reason=report['reason'])
        if self._cancel.is_set() and outcome not in {'completed', 'cancelled', 'running_unknown'}:
            outcome = 'running_unknown' if self._dispatched else 'cancelled'
            payload = dict(ok=False, code=outcome, reason='user_cancelled')
        if not all(cleanup.values()):
            outcome, payload = 'cleanup_failed', dict(ok=False, code='cleanup_failed',
                                                     result_retained=bool(payload.get('artifact')))
        self._state(outcome, **payload, cleanup=cleanup, finished_at=time.time(),
                    operation_dispatched=self._dispatched, watchdog=report)
        return json.loads(json.dumps(self.receipt))

    def _prepare(self, expression):
        copied = self.directory / self.runtime.context.name
        with self.runtime.context.open('rb') as source:
            _write(copied, source.read())
        if hashlib.sha256(copied.read_bytes()).hexdigest() != self.runtime.context_sha256:
            raise Unavailable('context_checksum')
        libraries = ''.join('DEFINE ' + name + ' "' + str(Path(path).resolve()) + '"\n'
                            for name, path in sorted(self.runtime.libraries.items()))
        _write(self.directory / 'cds.lib', libraries.encode())
        log = os.fdopen(open_private(self.directory / 'logs/router.jsonl',
                                     os.O_CREAT | os.O_EXCL | os.O_WRONLY), 'w', buffering=1)
        self.broker = ContextBroker(self.startup_timeout, operation_timeout=self.execution_timeout, diagnostic=log)
        self.broker.expected_identity = (self.receipt['instance_id'], self.receipt['generation'])
        self.router = self.broker.registry.register(*self.broker.expected_identity)
        self.receipt.update(bridge_id=self.broker.bridge_id, router_id=self.router.router_id,
                            port=self.broker.address[1], version=self.runtime.version,
                            context_sha256=self.runtime.context_sha256)
        self.journal = RouterJournal(self.directory / 'router', **{k: self.receipt[k]
                                    for k in ('instance_id', 'generation', 'bridge_id', 'router_id')})
        self.router.journal = self.journal
        snapshot = dict(valid=True, cwd=str(self.directory), execution_mode='background',
                        worker_id=self.worker_id, job_id=self.job_id, cellview=self.receipt['target'],
                        capabilities=['get_context', 'assistant_call'])
        self.context = BoundContext(self.receipt['instance_id'], self.receipt['generation'],
                                    self.receipt['target_id'], snapshot)
        job = dict(protocol=PROTOCOL, context=self.context.record(), target=self.receipt['target'],
                   **{k: self.receipt[k] for k in IDENTITY}, version=self.runtime.version,
                   context_name=self.runtime.context.name, boot_id=boot_id())
        _write(self.directory / 'job.json', job)
        connection = dict(host=self.broker.address[0], port=self.broker.address[1], token=self.broker.token,
                          supervisor_pid=os.getpid(), supervisor_start=_process_start(os.getpid()),
                          skill_timeout=self.execution_timeout)
        _write(self.directory / 'connection.json', connection)
        relay_command = shlex.join(agent_command('background-relay', '--connection-file',
                                                 str(self.directory / 'connection.json')))
        # Private generated invocation only. All worker implementation is in the context.
        args = [relay_command, json.dumps({'context': self.context.record()}, separators=(',', ':')),
                json.dumps(snapshot, separators=(',', ':')), self.context.target_id,
                self.runtime.version, expression, expression.encode().hex()]
        code = ('if(errset(progn(\n'
                'unless(loadContext(' + json.dumps(str(copied)) + ' t) error("Context unavailable"))\n'
                'aiBackgroundStart(' + ' '.join(json.dumps(value) for value in args) + ')\n'
                ') t) then t else exit(1))\n')
        _write(self.directory / 'bootstrap.il', code.encode())

    def _start(self):
        from .background_environment import guardian_environment

        command = [str(self.runtime.virtuoso), '-64', '-nograph', '-nocdsinit',
                '-cdslib', str(self.directory / 'cds.lib'), '-restore', str(self.directory / 'bootstrap.il'),
                '-log', str(self.directory / 'logs/CDS.log')]
        watch = self.directory / 'watch.json'
        _write(watch, dict(**{k: self.receipt[k] for k in ('job_id', 'worker_id', 'instance_id', 'generation')},
                          supervisor=process_identity(os.getpid()), command=command,
                          startup_timeout=self.startup_timeout, shutdown_timeout=self.shutdown_timeout))
        with os.fdopen(open_private(self.directory / 'logs/guardian.log',
                                     os.O_CREAT | os.O_EXCL | os.O_WRONLY), 'wb') as log:
            self.guardian = subprocess.Popen(agent_command('background-watch', '--connection-file', str(watch)),
                cwd=self.directory,
                env=guardian_environment(self.directory, self.environment),
                stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True, umask=0o077)
        deadline = time.monotonic() + self.startup_timeout
        launched = False
        while True:
            self._check_cancel()
            if self.guardian.poll() is not None:
                raise Unavailable('guardian_unavailable')
            if time.monotonic() >= deadline:
                raise TimeoutError('Guardian launch timeout')
            try:
                watcher = strict_json(_read(self.directory / 'watcher.json'))
            except FileNotFoundError:
                time.sleep(.02)
                continue
            if (watcher.get('job_id') != self.job_id or watcher.get('worker_id') != self.worker_id
                    or watcher.get('custodian', watcher['guardian'])['pid'] != self.guardian.pid):
                raise Unavailable('guardian_identity')
            if watcher['phase'] == 'started':
                self.process = watcher['launcher']
                from cadai import process_monitor

                self.process_record = process_monitor.track(
                    self.process['pid'], command, self.directory, name="Virtuoso · 后台检查",
                    session_id=self.process_session,
                    logs=(self.directory / 'logs/stdout.log', self.directory / 'logs/stderr.log',
                          self.directory / 'logs/CDS.log'),
                )
                self._state('starting', pid=self.process['pid'], launcher_pid=self.process['pid'],
                            launcher_start=self.process['start'], boot_id=watcher['boot_id'],
                            guardian_pid=self.guardian.pid)
                return
            if not launched:
                _write(self.directory / 'launch', b'1')
                launched = True
            time.sleep(.02)

    def _health(self):
        deadline = time.monotonic() + self.startup_timeout
        while True:
            self._check_cancel()
            if not process_alive(self.process):
                raise Unavailable('startup_exit')
            if self.guardian.poll() is not None:
                raise Unavailable('guardian_unavailable')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Registration timeout')
            try:
                context = self.broker.context(self.context.instance_id, self.context.target_id,
                                              timeout=min(.05, remaining))
                break
            except TimeoutError:
                pass
        if context != self.context:
            raise Unavailable('registration_identity')
        self.broker.register_session(self.receipt['session_id'], self.context)
        result = self._call('get_context', None, max(.001, deadline - time.monotonic()))
        actual_pid = result.get('pid')
        if (type(actual_pid) is not int or actual_pid <= 1
                or os.getpgid(actual_pid) != self.process['pid']):
            raise Unavailable('health_process_identity')
        expected = dict(self.context.snapshot, pid=actual_pid, version=self.runtime.version)
        if result != expected:
            raise Unavailable('health_identity')
        self._state('starting', pid=actual_pid, process_start=_process_start(actual_pid))

    def _call(self, method, params, timeout):
        peer = self.broker.peers[self.context.instance_id]
        peer.operation_timeout = timeout
        def dispatch(request_id):
            self._check_cancel()
            self._state(self.receipt['state'], **{
                'request_id' if method != 'get_context' else 'health_request_id': request_id})
            self._check_cancel()
            self._dispatched = method != 'get_context'
            if self._dispatched:
                _write(self.directory / 'execution.json', dict(
                    **{k: self.receipt[k] for k in ('job_id', 'worker_id', 'instance_id', 'generation')},
                    request_id=request_id, deadline_monotonic=time.monotonic() + timeout))
            future = Future()
            def call():
                try:
                    future.set_result(peer.call(self.context, method, params, request_id))
                except Exception as exc:
                    future.set_exception(exc)
            self._call_thread = threading.Thread(target=call, name='background-call', daemon=True)
            self._call_thread.start()
            while not future.done():
                if self._cancel.wait(.02) and not future.done():
                    # The transport may still return after the caller stops waiting.
                    # Retain unknown in the router journal before process teardown.
                    self.router.mark_unknown(request_id)
                    self._check_cancel()
            try:
                result = future.result()
            except (TimeoutError, OSError, EOFError):
                self.router.mark_unknown(request_id)
                raise
            if self._dispatched:
                _write(self.directory / 'execution-finished', b'1')
            return result
        return self.router.execute(method, dispatch, wait=True, execution_timeout=timeout,
                                   execution_mode='background', session_id=self.receipt['session_id'],
                                   target_id=self.context.target_id, job_id=self.job_id, worker_id=self.worker_id)

    def _cleanup(self):
        cleanup = dict(process_reaped=True, guardian_reaped=True, broker_closed=True, temporary_removed=True)
        if self.guardian:
            # A failed cooperative-stop write must never bypass process teardown.
            try:
                _write(self.directory / 'stop', b'1')
            except OSError:
                try:
                    self.guardian.terminate()
                except ProcessLookupError:
                    pass
            try:
                resume_owned_guardian(self.guardian)
                self.guardian.wait(timeout=max(2, 3 * self.shutdown_timeout + 1))
            except (OSError, subprocess.TimeoutExpired):
                cleanup['guardian_reaped'] = False
                # A local subreaper retains escaped children until confirmed dead.
                # Killing it would discard the sole descendant ownership evidence.
            report = self._watchdog_report()
            cleanup['process_reaped'] = bool(report and report.get('process_stopped'))
            if not self.process and not (self.directory / 'launch').exists():
                cleanup['process_reaped'] = True
            if report:
                self.receipt['exit_code'] = report.get('exit_code')
        if self.broker:
            try:
                self.broker.close()
            except (OSError, ValueError):
                cleanup['broker_closed'] = False
        if self.journal:
            if self._call_thread:
                self._call_thread.join(timeout=2)
                if self._call_thread.is_alive():
                    cleanup['broker_closed'] = False
            try:
                self.journal.close()
            except OSError:
                cleanup['broker_closed'] = False
        if not cleanup['process_reaped'] or not cleanup['guardian_reaped']:
            cleanup['temporary_removed'] = False
            return cleanup
        from cadai import process_monitor

        process_monitor.finish(self.process_record, self.receipt.get('exit_code'))
        try:
            for name in ('connection.json', 'watch.json', 'bootstrap.il', 'stop', 'launch', self.runtime.context.name):
                (self.directory / name).unlink(missing_ok=True)
            shutil.rmtree(self.directory / 'scratch')
            sync_directory(self.directory)
        except OSError:
            cleanup['temporary_removed'] = False
        return cleanup

    def _watchdog_report(self):
        try:
            report = strict_json(_read(self.directory / 'watchdog.json'))
            if any(report.get(k) != self.receipt[k] for k in ('job_id', 'worker_id', 'instance_id', 'generation')):
                return None
            return report
        except (OSError, ValueError):
            return None


def read_background_artifact(directory):
    """Read retained evidence only; this path never starts or resumes a worker."""
    directory = Path(directory)
    try:
        receipt = strict_json(_read(directory / 'receipt.json'))
        job = strict_json(_read(directory / 'job.json'))
        if (receipt.get('protocol') != PROTOCOL or receipt.get('state') != 'completed'
                or receipt.get('ok') is not True or any(receipt[k] != job[k] for k in IDENTITY)):
            raise ValueError('Background receipt identity/state mismatch')
        ref = receipt['artifact']
        if ref['path'] != 'artifacts/result.json' or not stat.S_ISDIR((directory / 'artifacts').lstat().st_mode):
            raise ValueError('Invalid artifact path')
        data = _read(directory / ref['path'])
        if len(data) != ref['size'] or hashlib.sha256(data).hexdigest() != ref['sha256']:
            raise ValueError('Background artifact checksum mismatch')
        result = strict_json(data)
        if (any(result[k] != receipt[k] for k in IDENTITY + ('request_id',))
                or result.get('protocol') != PROTOCOL or result.get('snapshot_scope') != 'saved_oa'
                or result.get('inspection') != result['evidence'].get('inspection')
                or not assess_background_probe(json.dumps(result['evidence']), target=job['target'],
                                                expected_version=job['version'])['available']):
            raise ValueError('Background artifact evidence mismatch')
        return result
    except (OSError, KeyError, TypeError) as exc:
        raise ValueError('Background artifact unavailable or invalid') from exc


def _process_start(pid):
    return Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()[19]


def background_relay(connection_file):
    """Private IPC child, sharing no targets or connection with the foreground."""
    from ..transport.relay import relay
    config = strict_json(_read(Path(connection_file)))
    relay(config['host'], config['port'], config['token'], skill_timeout=config['skill_timeout'])
    return 0


def background_watch(connection_file):
    from .background_watchdog import watch_worker
    from .background_custody import run
    return run(watch_worker, connection_file)
