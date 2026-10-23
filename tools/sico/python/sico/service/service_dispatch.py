"""Bounded background command adapter; socket detach never cancels accepted work."""

import queue
import threading
from concurrent.futures import Future

from ..transport.framing import ProtocolError
from ..core.ask_queue import SERVICE_CAPACITY
from .cleanup_task import DEFAULT_CLEANUP_SECONDS, CleanupTask
from .service_business import ServiceBusiness
from .service_lifecycle import PendingWork
from .service_protocol import result_message
from .session_control import ControlRefused, SessionControl

REFUSAL_CAPACITY = 256  # Listener: at most 32 clients with eight outstanding frames each.


class ServiceDispatch:
    def __init__(self, owner, descriptor, *, capacity=SERVICE_CAPACITY):
        from .frontend_hub import FrontendHub

        self.control = SessionControl(owner, descriptor)
        self.frontend = FrontendHub(owner, descriptor, self.control)
        self._business = ServiceBusiness(owner, descriptor, self.control)
        self._owner = owner
        self._queue = queue.Queue(maxsize=capacity + REFUSAL_CAPACITY)
        self._capacity = capacity
        self._pending = 0
        self._refusals = 0
        self._closing = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="copilot-service-commands")
        self._cleanup = CleanupTask(self._release, "copilot-dispatch-close")
        self._thread.start()

    def submit(self, request, connection=None):
        future = Future()
        with self._lock:
            code = ("unavailable" if self._closing else
                    "capacity" if self._pending >= self._capacity else None)
            if code is None:
                self._pending += 1
                self._queue.put_nowait((request, future, connection, False))
            elif (code == "capacity" and request["method"] == "host.submit"
                  and self._refusals < REFUSAL_CAPACITY):
                # Refusals share the sole admission authority. No bridge I/O or
                # durable writes run on the socket listener, even under pressure.
                self._refusals += 1
                self._queue.put_nowait((request, future, connection, True))
                return future
        if code == "capacity" and request["method"] == "host.submit":
            future.set_result(result_message(request,
                self._business.failure(request, "unknown", "unconfirmed")))
            return future
        if code is not None:
            future.set_result(result_message(request,
                self._business.failure(request, "rejected", code)))
        return future

    def pending_work(self):
        with self._lock:
            pending = self._pending
            refusals = self._refusals
        frontend_pending = self.frontend.pending
        # Observe admission first: a completing open publishes its session before
        # decrementing this count. The reverse order could report a false idle gap.
        work = self._owner.pending_work()
        # Historical recovery evidence owns no current-process resources. Keep it
        # queryable on disk without making archived sessions block service stop.
        # Includes admitted creation commands before a session has been published.
        return PendingWork(work.sessions, work.queued, work.approvals,
                           work.jobs + pending + refusals + frontend_pending
                           + int(self.frontend.workspace.background.busy),
                           work.reconcile)

    def _execute(self, request, connection, refusal=False):
        try:
            if refusal:
                return self._business.host.refuse_capacity(request).record()
            return self._business.execute(request, connection)
        except ProtocolError:
            return {"error": "protocol_error"}
        except ControlRefused:
            return self._business.failure(request, "rejected", "control_required")
        except ValueError:
            return self._business.failure(request, "rejected", "input_rejected")
        except Exception:
            # No raw exception, environment, token or backend resource crosses the wire.
            return self._business.failure(request, "unknown", "unconfirmed")

    def _run(self):
        try:
            while True:
                try:
                    request, future, connection, refusal = self._queue.get(timeout=0.05)
                except queue.Empty:
                    with self._lock:
                        if self._closing:
                            break
                    continue
                try:
                    future.set_result(result_message(request, self._execute(request, connection,
                                                                           refusal)))
                finally:
                    with self._lock:
                        if refusal:
                            self._refusals -= 1
                        else:
                            self._pending -= 1
        finally:
            self._cleanup.start()

    def _release(self):
        self._thread.join()
        self.frontend.close(None)
        self._owner.close(None)
        self.frontend.attach.close()

    def close(self):
        with self._lock:
            self._closing = True
        self.frontend.close(0)
        self._cleanup.start()

    def wait(self, timeout=None):
        return self._cleanup.wait(timeout)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        self.wait(DEFAULT_CLEANUP_SECONDS)
