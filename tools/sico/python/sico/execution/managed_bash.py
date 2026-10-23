"""Explicit TerminalBackend over a managed relay and one persistent Bash.

Composition must supply reviewed binary/control paths and a session environment.
The pipes/pty modes are explicit; no product default, unsandboxed fallback or
restart is chosen. PTY input is bounded and only active after the dispatcher
commits an owner.
"""

import base64
from collections import deque
import os
from pathlib import Path
import sys
import threading
import time
import uuid

from ..codex.exec_policy import process_environment
from ..codex.exec_process import ManagedExecProcess
from ..core.terminal_commands import CommandRequest, exact_fields
from ..core.terminal_input import TerminalInput
from ..core.terminal_session import TerminalGeneration, TerminalRetirement
from ..installation import python_path
from ..service.terminal_backend import TerminalCompletion
from . import relay_wire as wire


class ManagedBashBackend:
    def __init__(self, executable, control_environment, *, cwd, home, environment,
                 environment_id, access, writable_roots=()):
        self._environment = process_environment(environment)
        self._environment_id, self._cwd = environment_id, str(Path(cwd).resolve())
        self._process = ManagedExecProcess(executable, control_environment, cwd=cwd, home=home,
                                           access=access, writable_roots=writable_roots)
        self._lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._executing = threading.Lock()
        self._stopped = threading.Event()
        self._attempt = self._generation = self._retirement = None
        self._namespace = self._namespace_pid = None
        self._failed = False
        self._mode = self._active_ref = None
        self._decoder, self._pending = wire.decoder(), deque()

    def _send(self, value):
        with self._send_lock:
            payload = wire.encode(value)
            # process/write has a tighter raw-block bound than a complete frame.
            for offset in range(0, len(payload), 65536):
                if self._stopped.is_set():
                    raise RuntimeError('Managed Bash owner stopped')
                self._process.write(payload[offset:offset + 65536])

    def _next(self, deadline=None):
        while not self._pending:
            if self._stopped.is_set():
                raise RuntimeError('Managed Bash owner stopped')
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError('Managed Bash observation deadline expired')
            event = self._process.read(timeout=.1)
            if event is None:
                return None
            if event.kind == 'output' and event.stream == 'stdout':
                self._pending.extend(self._decoder.feed(event.data))
                if len(self._pending) > 128:
                    raise ValueError('Relay observation queue exceeds budget')
            elif event.kind == 'output':
                raise RuntimeError('Managed Bash relay reported a runtime error')
            else:
                raise RuntimeError('Managed Bash relay ended before a command boundary')
        return self._pending.popleft()

    def _wait(self, deadline):
        while True:
            message = self._next(deadline)
            if message is not None:
                return message

    def start(self, attempt, claim):
        with self._lock:
            if self._attempt is not None or self._stopped.is_set():
                raise RuntimeError('Managed Bash creation is occupied or closed')
            if (claim.mode not in ('pipes', 'pty') or claim.shell != '/bin/bash'
                    or str(Path(claim.cwd).resolve()) != self._cwd
                    or claim.environment_id != self._environment_id or claim.ref != attempt.ref):
                raise ValueError('Claim does not match the managed Bash scope')
            self._attempt = attempt
            self._mode = claim.mode
            # The relay needs product imports; only the explicit user environment
            # is passed onward to Bash, so helper PYTHONPATH cannot leak into it.
            helper = dict(PATH='/usr/bin:/bin', HOME=self._environment.get('HOME', self._cwd),
                          PYTHONPATH=python_path(), PYTHONDONTWRITEBYTECODE='1')
            self._process.start([sys.executable, '-B', '-s', '-m', 'sico.execution.bash_relay'], helper)
            self._send(dict(kind='initialize', environment=self._environment,
                            mode=self._mode))
            ready = self._wait(time.monotonic() + 5)
            exact_fields(ready, ('kind', 'command', 'pid', 'namespace'))
            if ready['kind'] != 'ready' or ready['command'] is not None:
                raise ValueError('Managed Bash did not establish a control channel')
            observed = self._process.observe_process(ready['pid'], ready['namespace'], '/bin/bash')
            if observed.cwd != self._cwd:
                raise ValueError('Managed Bash started in a different directory')
            self._namespace_pid, self._namespace = ready['pid'], ready['namespace']
            self._generation = TerminalGeneration(attempt, observed.pid, observed.start)
            return self._generation

    def execute(self, request, *, started, output, cancelled):
        if not self._executing.acquire(blocking=False):
            raise RuntimeError('Managed Bash already has an active command')
        try:
            if (self._generation is None or self._stopped.is_set() or self._failed or not isinstance(request, CommandRequest)
                    or request.ref.session_id != self._attempt.ref.session_id
                    or request.ref.terminal_id != self._attempt.ref.terminal_id
                    or request.mode not in ('pipes', 'pty')
                    or self._mode is not None and request.mode != self._mode
                    or request.shell not in (None, '/bin/bash')
                    or request.environment_id != self._environment_id):
                raise ValueError('Command does not match the managed Bash generation')
            if cancelled():
                raise RuntimeError('Command was cancelled before delivery')
            self._active_ref = request.ref
            self._send(dict(kind='execute', request=request.record()))
            return self._execute(request, started, output, cancelled)
        except BaseException:
            self._failed = True
            raise
        finally:
            self._active_ref = None
            self._executing.release()

    def write_input(self, value):
        if (not isinstance(value, TerminalInput) or self._mode != 'pty'
                or self._generation is None or self._stopped.is_set() or self._failed
                or self._active_ref != value.ref):
            raise ValueError('Terminal input requires the active PTY command')
        self._send(dict(kind='input', input=value.record()))

    def _prepared(self, message, request):
        exact_fields(message, ('kind', 'command', 'pid', 'namespace'))
        if (message['pid'], message['namespace']) != (self._namespace_pid, self._namespace):
            raise ValueError('Command belongs to a different shell')
        observed = self._process.observe_process(message['pid'], message['namespace'], '/bin/bash')
        if (observed.pid, observed.start) != (self._generation.pid, self._generation.process_start):
            raise ValueError('Host shell identity changed')
        if request.cwd is not None and observed.cwd != os.path.realpath(request.cwd):
            raise ValueError('Command directory was not established')
        context = dict(cwd=observed.cwd, generation=self._generation.record())
        self._send(dict(kind='continue', command=request.ref.command_id))
        return context

    def _execute(self, request, started, output, cancelled):
        context, running, stopping, cancel_sent = None, False, False, False
        deadline = time.monotonic() + 5
        while True:
            if cancelled() and not stopping:
                stopping = True
                if self._mode == 'pipes':
                    self._process.terminate()
                deadline = time.monotonic() + 5
            if stopping and self._mode == 'pty' and running and not cancel_sent:
                self._send(dict(kind='cancel', command=request.ref.command_id))
                cancel_sent = True
            message = self._next(deadline)
            if message is None:
                continue
            if message.get('command') != request.ref.command_id:
                raise ValueError('Relay observation belongs to another command')
            kind = message.get('kind')
            if kind == 'prepared' and context is None and not stopping:
                context = self._prepared(message, request)
            elif kind == 'started' and context is not None and not running:
                exact_fields(message, ('kind', 'command'))
                started(uuid.uuid4().hex, context)
                running, deadline = True, None if not stopping else deadline
            elif kind == 'output' and running:
                exact_fields(message, ('kind', 'command', 'stream', 'data'))
                data = base64.b64decode(message['data'], validate=True)
                streams = ('pty',) if self._mode == 'pty' else ('stdout', 'stderr')
                if (message['stream'] not in streams or not 1 <= len(data) <= 65536
                        or base64.b64encode(data).decode() != message['data']):
                    raise ValueError('Invalid attributed output')
                output(message['stream'], data)
            elif kind == 'complete' and running and (not stopping or self._mode == 'pty'):
                fields = ('kind', 'command', 'exit_code', 'cancelled') if self._mode == 'pty' else (
                    'kind', 'command', 'exit_code')
                exact_fields(message, fields)
                if self._pending or self._decoder.buffer:
                    raise ValueError('Unexpected relay data after command completion')
                observed = self._process.observe_process(self._namespace_pid, self._namespace, '/bin/bash')
                if (observed.pid, observed.start) != (self._generation.pid, self._generation.process_start):
                    raise ValueError('Completed command no longer has the same shell')
                return TerminalCompletion(message['exit_code'], output_complete=True,
                                          shell_ready=True,
                                          cancelled=bool(message.get('cancelled', False)))
            elif kind == 'failed':
                exact_fields(message, ('kind', 'command'))
                raise RuntimeError('Managed Bash command boundary could not be confirmed')
            else:
                raise RuntimeError('Managed Bash command boundary could not be confirmed')

    def close(self, attempt):
        with self._lock:
            if self._attempt is not None and attempt != self._attempt:
                raise ValueError('Cannot retire another managed Bash attempt')
            self._stopped.set()
            self._active_ref = None
            if self._retirement is not None:
                return self._retirement
            self._process.close()  # Acknowledged supervisor cleanup covers all descendants.
            kind = 'process_exited' if self._generation is not None else 'owner_stopped'
            self._retirement = TerminalRetirement(attempt, kind, uuid.uuid4().hex, self._generation)
            return self._retirement
