"""Persistent async frontend lanes; encoding and socket waits never run on Qt."""

import queue
import threading
import time
import uuid
from concurrent.futures import CancelledError, Future
from contextlib import ExitStack

from ..transport.framing import ProtocolError
from .frontend_assets import result_budget
from .frontend_codec import (
    CHUNK_BYTES,
    PAYLOAD_BYTES,
    decode_chunk,
    decode_payload,
    digest,
    encode_chunk,
    encode_payload,
)
from .service_channel import service_channel
from .service_completion import ServiceCompletion
from .service_errors import RequestUnsent, ResultUnknown
from .service_messages import ServiceRequest
from .service_protocol import exact_fields


class _Pending:
    def __init__(self, operation, params, publish, key, discard):
        self.ready = Future()
        self.operation, self.params, self.publish = operation, params, publish
        self.key, self.discard = key, discard
        self.abandoned = threading.Event()
        self.retired = False
        self.value = None
        self.lock = threading.Lock()

    def abandon(self):
        self.abandoned.set()
        self.ready.cancel()
        self.retire()

    def retire(self):
        with self.lock:
            if self.retired or self.value is None:
                return
            self.retired = True
            value = self.value
        if self.discard:
            self.discard(value)


class _Completion(ServiceCompletion):
    def __init__(self, item, closed, validate):
        super().__init__(item, closed)
        self._item = item
        self._validate = validate

    def result(self, timeout=None):
        value = super().result(timeout)
        if self._validate is not None:
            self._validate()
        return value

    def discard(self):
        self._item.abandon()
        super().discard()

    def cancel(self):
        self._item.abandon()
        return super().cancel()

    def add_done_callback(self, callback):
        self._item.ready.add_done_callback(lambda _future: callback(self))


class FrontendLane:
    def __init__(self, descriptor, client_id, method, *, timeout=120):
        self.descriptor, self.client_id, self.method = descriptor, client_id, method
        self.timeout = timeout
        self.queue = queue.Queue(maxsize=32)
        self.closed = threading.Event()
        self.retiring = threading.Event()
        self.state, self.instructions = "idle", ""
        self.connection_id = None
        self.epoch = 0
        self.thread = threading.Thread(target=self._run, name="copilot-frontend-client",
                                       daemon=True)
        self.thread.start()

    def request(self, operation, params, publish=lambda value: value, *, operation_id=None,
                discard=None, validate=None, control=None):
        if self.closed.is_set():
            raise RuntimeError("服务连接已分离，请重新连接")
        item = _Pending(operation, params, publish, operation_id or uuid.uuid4().hex, discard)
        item.control = control
        item.epoch = self.epoch
        try:
            self.queue.put_nowait(item)
        except queue.Full:
            raise ValueError("服务客户端请求队列已满") from None
        return _Completion(item, self.closed, validate)

    def close(self):
        self.state = "detached"
        self.closed.set()

    def retire_connection(self):
        """An overloaded cleanup queue retires its connection and all owned subscriptions."""
        self.retiring.set()

    def _cancelled(self):
        return self.closed.is_set() or self.retiring.is_set()

    def _run(self):
        with ExitStack() as resources:
            channel = None
            while not self.closed.is_set():
                if self.retiring.is_set():
                    channel = None
                    self.epoch += 1
                    self.state = "disconnected"
                    resources.close()
                    self.retiring.clear()
                try:
                    item = self.queue.get(timeout=0.5)
                except queue.Empty:
                    if channel is not None:
                        try:
                            channel.heartbeat(deadline=time.monotonic() + 2,
                                              cancelled=self._cancelled)
                        except Exception:
                            self.state = "disconnected"
                            channel = None
                            self.epoch += 1
                            resources.close()
                    continue
                if item.ready.cancelled():
                    continue
                try:
                    if item.epoch != self.epoch:
                        raise ValueError("原连接请求已失效，请重新打开页面")
                    if channel is None:
                        self.state = "reconnecting" if self.epoch else "connecting"
                        channel = resources.enter_context(service_channel(
                            self.descriptor, deadline=time.monotonic() + 5,
                            client_id=self.client_id, cancelled=self._cancelled,
                            purpose="attach"))
                    self.state = "connected"
                    self.connection_id = channel.identity.connection_id
                    value = item.publish(self._exchange(channel, item))
                    item.value = value
                    if self.closed.is_set() or item.abandoned.is_set() or item.ready.cancelled():
                        item.retire()
                    else:
                        item.ready.set_result(value)
                except Exception as exc:
                    if isinstance(exc, ProtocolError) and self.method == "frontend.command":
                        exc = ProtocolError(str(exc) + ": " + item.key)
                    if item.abandoned.is_set() or self.closed.is_set():
                        item.retire()
                    if not isinstance(exc, ValueError) or isinstance(exc, ProtocolError):
                        self.state = "disconnected"
                        channel = None
                        self.epoch += 1
                        resources.close()
                    if not item.ready.done():
                        item.ready.set_exception(exc)
            self.state = "detached"
            while True:
                try:
                    self.queue.get_nowait().ready.cancel()
                except queue.Empty:
                    break

    def _exchange(self, channel, item):
        deadline = time.monotonic() + self.timeout
        limit = result_budget(item.operation) if self.method == "frontend.query" else PAYLOAD_BYTES
        started = False

        def call(**values):
            try:
                return channel.exchange(ServiceRequest(self.method, item.key, values,
                                        getattr(item, "control", None)),
                    deadline=deadline, cancelled=self._cancelled).status.value
            except RequestUnsent as exc:
                if started:
                    raise ResultUnknown(item.key, item.operation) from exc
                raise
            except ProtocolError as exc:
                if self.method == "frontend.command":
                    raise ProtocolError(str(exc) + ": " + item.key) from exc
                raise

        params = item.params() if callable(item.params) else item.params
        payload = encode_payload(dict(operation=item.operation, params=params))
        if item.ready.cancelled():
            raise CancelledError()
        if call(step="reset") != {"state": "empty"}:
            raise ProtocolError("Invalid frontend reset acknowledgement")
        for offset in range(0, len(payload), CHUNK_BYTES):
            end = min(len(payload), offset + CHUNK_BYTES)
            if call(step="put", offset=offset, data=encode_chunk(payload[offset:end])) != {
                    "state": "upload", "size": end}:
                raise ProtocolError("Invalid frontend upload acknowledgement")
        row = call(step="run", size=len(payload), digest=digest(payload))
        started = True
        self._pending(row)
        output, manifest = bytearray(), None
        while True:
            if time.monotonic() >= deadline:
                raise ResultUnknown(item.key, item.operation)
            row = call(step="read", offset=len(output))
            if row.get("state") == "pending":
                self._pending(row)
                self.closed.wait(0.03)
                continue
            exact_fields(row, {"state", "size", "digest", "offset", "data"})
            if (row["state"] != "ready" or type(row["size"]) is not int
                    or not 0 < row["size"] <= limit or type(row["offset"]) is not int
                    or row["offset"] != len(output) or not isinstance(row["digest"], str)):
                raise ProtocolError("Invalid frontend result manifest")
            current = row["size"], row["digest"]
            if manifest is not None and manifest != current:
                raise ProtocolError("Frontend result changed during transfer")
            manifest = current
            chunk = decode_chunk(row["data"])
            if len(chunk) != min(CHUNK_BYTES, row["size"] - len(output)):
                raise ProtocolError("Frontend result chunk is incomplete")
            output.extend(chunk)
            if len(output) == row["size"]:
                break
        if digest(output) != manifest[1]:
            raise ProtocolError("Frontend result digest mismatch")
        value = decode_payload(output, limit=limit)
        if value.get("ok") is True:
            exact_fields(value, {"ok", "value"})
            return value["value"]
        exact_fields(value, {"ok", "kind", "message"})
        if value["ok"] is not False or value["kind"] not in {"protocol", "rejected", "unknown"}:
            raise ProtocolError("Invalid frontend failure")
        if not isinstance(value["message"], str) or len(value["message"]) > 512:
            raise ProtocolError("Invalid frontend failure message")
        if value["kind"] == "protocol":
            raise ProtocolError(value["message"] + ": " + item.key)
        if value["kind"] == "unknown":
            raise ResultUnknown(item.key, item.operation, value["message"])
        raise ValueError(value["message"])

    def _pending(self, row):
        exact_fields(row, {"state", "instructions"})
        if row["state"] != "pending" or not isinstance(row["instructions"], str):
            raise ProtocolError("Invalid frontend progress")
        self.instructions = row["instructions"]
