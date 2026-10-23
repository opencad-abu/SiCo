"""Own live interaction mailboxes, request identities and connection lifetime."""

import json
import uuid
from copy import deepcopy
from hashlib import sha256
from types import MappingProxyType

from .rpc import RpcError


class InputIdentityConflict(RpcError):
    """A duplicate changes identity; reject without disturbing other children."""


def request_id(value):
    return (type(value) is int and -(2 ** 63) <= value < 2 ** 64
            or isinstance(value, str) and 0 < len(value) <= 200)


class InputRequests:
    def __init__(self, *, lock, task, rpc, thread, child_current, emit, withdraw):
        self._lock, self._task, self._rpc = lock, task, rpc
        self._thread, self._child_current = thread, child_current
        self._emit, self._withdraw = emit, withdraw
        self._connection, self._connection_id = None, ''
        self._epoch = 0
        self._registering = set()
        self._seen, self._closed, self._pending, self._host_delivered = {}, set(), {}, {}
        self._project()

    @property
    def connection(self):
        return self._connection

    @property
    def connection_id(self):
        return self._connection_id

    @property
    def seen(self):
        with self._lock:
            return MappingProxyType(dict(self._seen))

    def is_current(self):
        with self._lock:
            return self._connection is not None and self._rpc() is self._connection

    def is_closed(self, thread, rpc_id):
        with self._lock:
            return (thread, type(rpc_id), rpc_id) in self._closed

    def connect(self, *, required=True):
        with self._lock:
            rpc = self._rpc()
            if rpc is None:
                if self._connection is not None:
                    self._withdraw('原连接已结束；答复不会转发到新连接', send=False)
                    self.finish()
                    self._connection, self._connection_id = None, ''
                if required:
                    raise ValueError('No input connection')
                return ''
            if rpc is not self._connection:
                self._withdraw('原连接已结束；答复不会转发到新连接', send=False)
                self.finish()
                self._connection, self._connection_id = rpc, uuid.uuid4().hex
                self._seen.clear()
                self._closed.clear()
                try:
                    self._emit('codex.connection', {'connection_id': self._connection_id})
                except BaseException:
                    self.finish()
                    self._connection, self._connection_id = None, ''
                    raise
            return self._connection_id

    def snapshot(self):
        with self._lock:
            return MappingProxyType({key: deepcopy(value) for key, value in self._pending.items()})

    def _project(self):
        task = self._task()
        if task:
            blocking = [key for key, value in self._pending.items()
                        if value.get('blocking', True)]
            supplementary = [key for key, value in self._pending.items()
                             if not value.get('blocking', True)]
            if blocking:
                task['waiting_audits'] = blocking
            else:
                task.pop('waiting_audits', None)
            if supplementary:
                task['supplementary_audits'] = supplementary
            else:
                task.pop('supplementary_audits', None)

    def open(self, key, pending, publish, *, claim=None):
        with self._lock:
            if key in self._pending:
                raise ValueError('Interaction is already open')
            if claim is not None:
                self.remember(claim)
            self._pending[key] = deepcopy(pending)
            self._project()
            self._registering.add(key)
            try:
                publish()
            except BaseException:
                self._pending.pop(key, None)
                self._project()
                raise
            finally:
                self._registering.discard(key)

    def bind(self, key, pending, publish, *, claim=None):
        with self._lock:
            if self._pending[key]['rpc_id'] is not None:
                raise ValueError('Only a host interaction can acquire a native binding')
            if claim is not None:
                self.remember(claim)
            self._pending[key] = deepcopy(pending)
            self._registering.add(key)
            try:
                publish()
            except BaseException:
                self._pending.pop(key, None)
                self._project()
                raise
            finally:
                self._registering.discard(key)

    def retain(self, key, pending, publish):
        """Close a terminated native request while retaining its business question."""
        with self._lock:
            previous = self._pending[key]
            if previous['rpc_id'] is None or pending['rpc_id'] is not None:
                raise ValueError('Only a native request can become a retained review')
            binding = previous['binding']
            self.remember_closed((binding['threadId'], type(previous['rpc_id']), previous['rpc_id']))
            self._pending[key] = deepcopy(pending)
            self._project()
            try:
                publish()
            except BaseException:
                self._pending.pop(key, None)
                self._project()
                raise

    def resolve(self, key, publish, *, host_response=None):
        """Consume before persisting dispatch; a failed write never reopens an input."""
        with self._lock:
            pending = self._pending.pop(key, None)
            self._project()
            if pending is None:
                return None
            epoch = self._epoch
            publish()
            if host_response is not None and epoch == self._epoch:
                self._host_delivered[key] = deepcopy(host_response)
            return deepcopy(pending)

    def cancel(self, key, publish):
        return self.resolve(key, publish)

    def delivered_hosts(self):
        with self._lock:
            return tuple(self._host_delivered)

    def take_host(self, key):
        with self._lock:
            return self._host_delivered.pop(key, None)

    def finish(self):
        with self._lock:
            self._epoch += 1
            self._pending.clear()
            self._host_delivered.clear()
            self._project()

    def check(self, rpc_id, thread, value):
        with self._lock:
            self.connect()
            if not request_id(rpc_id):
                raise ValueError('Invalid server request id')
            key = (type(rpc_id), rpc_id)
            signature = sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                          allow_nan=False).encode('utf-8')).hexdigest()
            previous = self._seen.get(key)
            if previous and previous != (thread, signature):
                raise InputIdentityConflict('Server input identity changed; inspect before continuing')
            if self.is_closed(thread, rpc_id) or previous:
                return None
            if len(self._seen) >= 4096:
                raise RpcError('Server input identity limit reached; reopen the session')
            return self._connection_id, key, (thread, signature)

    def remember(self, claim):
        with self._lock:
            connection_id, key, value = claim
            if connection_id != self._connection_id or not self.is_current():
                raise ValueError('Input connection changed before registration')
            if (value[0], *key) in self._closed:
                raise ValueError('Input request ended before registration')
            if key in self._seen:
                raise InputIdentityConflict('Input request was already registered')
            self._seen[key] = value

    def discard(self, event):
        """Keep rejected/idle IDs closed for the lifetime of this connection."""
        with self._lock:
            params = event.get('params')
            if (not isinstance(params, dict) or not request_id(event.get('id'))
                    or not isinstance(params.get('threadId'), str)):
                return True
            self.connect()
            key = (type(event['id']), event['id'])
            closed = (params['threadId'], *key)
            if key in self._seen or closed in self._closed:
                return False
            self.remember_closed(closed)
            return True

    def remember_closed(self, key):
        with self._lock:
            if key not in self._closed and len(self._closed) >= 4096:
                raise RpcError('Server input identity limit reached; reopen the session')
            self._closed.add(key)

    def cancellation(self, pending):
        with self._lock:
            binding = pending.get("binding") or {}
            thread = binding.get("thread_id", binding.get("threadId"))
            rpc_id = pending["rpc_id"]
            if (rpc_id is None or not self.is_current()
                    or binding.get("connection_id") != self.connection_id or self.is_closed(thread, rpc_id)):
                return []
            result = {"result": {"action": "cancel"}} if pending.get("source") == "elicitation" else {
                "error": {"code": -32800, "message": "Input request is no longer active"}}
            return [(self.connection, {"id": rpc_id, **result})]

    def ignore(self, reason):
        self._emit("codex.input_ignored", {"reason": reason})

    def report_ignored(self, reason):
        self._emit("codex.elicitation.unsupported", {"reason": reason})

    def resolved(self, event, *, retain=None):
        if event.get('method') != 'serverRequest/resolved' or 'id' in event:
            return False
        params = event.get('params')
        if (not isinstance(params, dict) or not request_id(params.get('requestId'))
                or not isinstance(params.get('threadId'), str) or self._rpc() is None):
            return True
        with self._lock:
            self.connect()
            thread, rpc_id = params['threadId'], params['requestId']
            key = (type(rpc_id), rpc_id)
            previous = self._seen.get(key)
            if previous and previous[0] != thread:
                self._emit('codex.input_ignored', {'reason': 'foreign_resolved_thread'})
                return True
            if thread != self._thread() and not previous:
                try:
                    self._child_current(thread)
                except ValueError:
                    self._emit('codex.input_ignored', {'reason': 'unmatched_child_resolution'})
                    return True
            self.remember_closed((thread, *key))
            for audit_id, pending in self.snapshot().items():
                binding = pending.get('binding') or {}
                if (binding.get('thread_id', binding.get('threadId')) == thread
                        and type(pending['rpc_id']) is type(rpc_id) and pending['rpc_id'] == rpc_id):
                    if audit_id not in self._registering and retain is not None and retain(audit_id, pending):
                        continue
                    self._withdraw('服务已结束此交互；未交付的答复不会重发', keys=[audit_id], send=False)
        return True
