"""Design-target RPCs and cancellable requests, independent of desktop commands."""

from __future__ import annotations

import queue
import threading
from concurrent.futures import CancelledError, Future
from contextlib import nullcontext

from ..core.contracts import BoundContext
from ..transport.broker import skill_call_context


class TargetBridgeError(RuntimeError):
    """The bridge could not answer a target lookup or open request."""


class BridgeTargetProvider:
    def __init__(self, broker, context, session_id=None):
        self.broker, self.context = broker, context
        self.session_id = session_id

    def libraries(self, include_readonly=False):
        return self._rows("aiCopilotListLibraries", [bool(include_readonly)], "libraries", "name")

    def cells(self, library):
        return self._rows("aiCopilotListCells", [str(library)], "cells")

    def views(self, library, cell, family=None):
        values = [str(library), str(cell)]
        if family:
            family = tuple(family) if not isinstance(family, str) else (family,)
            values.append(
                "layout" if any(name.startswith("maskLayout") for name in family)
                else "schematic"
            )
        return self._rows("aiCopilotListViews", values, "views", "name", "view_type")

    def open_target(self, target):
        values = [
            str(target.get("library", "")), str(target.get("cell", "")),
            str(target.get("view", "")), str(target.get("view_type", "schematic")),
            "r" if target.get("read_only") else "a",
        ]
        data = self._call("aiCopilotOpenTarget", values)
        normalized = {key: data[key] for key in ("library", "cell", "view", "view_type")
                      if isinstance(data.get(key), str) and data[key]}
        return {**target, **normalized, "window": data.get("window", "")}

    def _rows(self, function, values, field, *keys):
        rows = self._call(function, values).get(field)
        if not isinstance(rows, list):
            raise TargetBridgeError("Bridge returned invalid target rows: " + field)
        for row in rows:
            valid = (
                isinstance(row, dict)
                and all(isinstance(row.get(key), str) and row[key] for key in keys)
            ) if keys else isinstance(row, str) and row
            if not valid:
                raise TargetBridgeError("Bridge returned invalid target rows: " + field)
        return rows

    def _call(self, function, values):
        if self.broker is None:
            raise TargetBridgeError("No bridge connection is available for this session")
        with skill_call_context(session_id=self.session_id) if self.session_id else nullcontext():
            reply = self.broker.circuit_call(self.context, {
                "function": function, "values": values, "claim": None, "expected": None,
            })
        data = reply.get("circuit") if isinstance(reply, dict) else None
        if not isinstance(data, dict):
            raise TargetBridgeError("Bridge returned an unrecognized target response")
        if data.get("ok") is False:
            message = data.get("message") or data.get("code") or "Bridge refused request"
            raise TargetBridgeError(str(message))
        return data


class TargetSource:
    """One picker's request scope. Only the target worker touches its provider."""

    def __init__(self, service, provider, validate=None):
        self._service, self._provider, self._validate = service, provider, validate
        self._closed = threading.Event()
        self.can_create = callable(getattr(provider, "create", None))

    def _check(self):
        if self._closed.is_set():
            raise CancelledError()
        if self._validate:
            self._validate()

    def _call(self, name, *args):
        self._check()
        result = getattr(self._provider, name)(*args)
        self._check()
        return result

    def libraries(self, include_readonly=False):
        return self._service._request(self._call, "libraries", bool(include_readonly))

    def cells(self, library):
        return self._service._request(self._call, "cells", library)

    def views(self, library, cell, family=None):
        return self._service._request(self._call, "views", library, cell, tuple(family or ()))

    def open_target(self, target):
        return self._service._request(self._call, "open_target", dict(target))

    def create(self, target):
        if not self.can_create:
            raise ValueError("Target creation is unavailable")
        return self._service._request(self._create, dict(target))

    def _create(self, target):
        created = self._call("create", target)
        if not created:
            raise TargetBridgeError("Target creation did not return a view")
        return self._call("open_target", {**target, **created})

    def close(self):
        self._closed.set()


class TargetService:
    """Bounded bridge queue; slow target lookups cannot delay control receipts."""

    def __init__(self):
        self._queue = queue.Queue(maxsize=64)
        self._lock = threading.Lock()
        self._pending = 0
        self._closing = False
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._run, name="copilot-targets", daemon=True)
        self._thread.start()

    def source(self, provider, validate=None):
        return TargetSource(self, provider, validate)

    def bind(self, broker, controller, *, task_id="", audit_id="", context=None, validate=None):
        context = context or BoundContext.from_record(controller.current.record())

        def check():
            if validate is not None:
                validate()
            controller.check_target_request(context, task_id, audit_id)

        return self.source(
            BridgeTargetProvider(broker, context, controller.session_id),
            check,
        )

    @property
    def busy(self):
        with self._lock:
            # Once closing starts, queued Futures are cancelled and any
            # active bridge call is detached from the UI teardown path.
            return bool(self._pending) and not self._closing

    def _request(self, operation, *args):
        future = Future()
        with self._lock:
            if self._closing:
                raise ValueError("Target service is closing")
            try:
                self._queue.put_nowait((future, operation, args))
            except queue.Full:
                raise ValueError("Target request queue is full") from None
            self._pending += 1
        return future

    def _run(self):
        try:
            while True:
                item = self._queue.get()
                if item is None:
                    return
                future, operation, args = item
                try:
                    if future.set_running_or_notify_cancel():
                        try:
                            future.set_result(operation(*args))
                        except Exception as exc:
                            future.set_exception(exc)
                finally:
                    with self._lock:
                        self._pending -= 1
        finally:
            self._stopped.set()

    def close(self):
        cancelled = []
        with self._lock:
            if self._closing:
                return
            self._closing = True
            while True:
                try:
                    future, _, _ = self._queue.get_nowait()
                except queue.Empty:
                    break
                cancelled.append(future)
                self._pending -= 1
            self._queue.put_nowait(None)
        for future in cancelled:
            future.cancel()

    def wait(self, timeout=None):
        """Wait for the dispatcher, bounded when a bridge call is still active.

        A running ``circuit_call`` belongs to the broker and cannot be
        interrupted safely from this queue.  Closing the desktop therefore
        cancels queued requests immediately and lets the daemon dispatcher
        finish the in-flight call without holding up controller shutdown.
        Callers that own a normal idle service can still wait without a
        timeout and get the same fully stopped guarantee as before.
        """
        self._thread.join(timeout)
        return self._stopped.is_set()
