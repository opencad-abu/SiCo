"""One-shot server replies: capture under the state lock, transmit outside it."""

import time
from copy import deepcopy

from .rpc import RpcError


def send_controls(replies):
    """Reject/close consumed requests within one short cleanup budget."""
    deadline = time.monotonic() + 1
    for connection, message in replies:
        connection.send(message, timeout=max(0, deadline - time.monotonic()),
                        cancelled=lambda: False)


class InputDelivery:
    """A consumed delivery claim with only validation and publication operations."""

    def __init__(self, key, pending, connection, result, emit, *, runtime, requests, row, status):
        self._key, self._emit, self._runtime, self._requests = key, emit, runtime, requests
        self._connection, self._row, self._status = connection, deepcopy(row), status
        self._pending = deepcopy(pending)
        scope = runtime.snapshot()
        self._turn_id, self._thread_id = scope.turn_id, scope.thread_id
        self._message = {'id': pending['rpc_id'], 'result': deepcopy(result)}
        self._used = False

    def _outdated(self):
        scope, pending, row = self._runtime.snapshot(), self._pending, self._row
        binding = pending['binding']
        thread = binding.get('thread_id', binding.get('threadId'))
        if (not scope.admitted or scope.task_id != row['task_id'] or scope.context != row['context']
                or not self._requests.is_current() or self._requests.connection is not self._connection
                or self._requests.connection_id != binding['connection_id']
                or self._requests.is_closed(thread, pending['rpc_id'])
                or self._status() != 'dispatching'):
            return True
        if pending.get('child_thread_id'):
            try:
                self._runtime.child_current(pending['child_thread_id'], turn_id=pending['child_turn_id'])
            except ValueError:
                return True
        elif scope.turn_id != self._turn_id or scope.thread_id != self._thread_id:
            return True
        return False

    def _unconfirmed(self):
        if (self._status() == 'dispatching'
                and self._row['task_id'] == self._runtime.snapshot().task_id):
            self._emit('unconfirmed', {'id': self._key, 'reason': '答复发送结果未确认；不会自动重发'})

    def send(self):
        with self._runtime.lock:
            if self._used:
                return 0
            self._used = True
            if self._outdated():
                self._unconfirmed()
                return 0
        try:
            self._connection.send(self._message, cancelled=lambda: not self._runtime.admitted())
        except (OSError, ValueError, RuntimeError) as exc:
            with self._runtime.lock:
                self._unconfirmed()
            raise RpcError('Input reply delivery is unconfirmed; no replay',
                           phase=getattr(exc, 'phase', None),
                           sent_bytes=getattr(exc, 'sent_bytes', None)) from exc
        with self._runtime.lock:
            if self._outdated():
                self._unconfirmed()
                raise RpcError('Input source changed during delivery; unconfirmed; no replay')
            self._emit('resumed', {'id': self._key, 'reply_id': self._row['reply']['reply_id'],
                                   'delivery': 'sent'})
            self._runtime.activity('waiting_model', '答复已发送，等待服务后续结果')
        return 1
