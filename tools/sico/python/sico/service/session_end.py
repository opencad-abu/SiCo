"""End one captured runtime without blocking client detach or sibling sessions."""

import threading
from collections import OrderedDict

from ..storage.session_release import save_release
from .cleanup_task import SESSION_END_SECONDS, CleanupTask, Deadline
from .control_contract import ControlRefused
from .session_end_contract import SessionEndView


class SessionEnd:
    def __init__(self, owner, control, *, retired=lambda _key: None,
                 timeout=SESSION_END_SECONDS):
        self.owner, self.control = owner, control
        self._lock = threading.Lock()
        self._views = OrderedDict()
        self._threads = set()
        self._retired = retired
        self.timeout = timeout

    def observe(self, address, operation_id):
        with self._lock:
            view = self._views.get((address, operation_id))
        if view is not None:
            return view
        controller = self.owner.controllers.get(address.session.session_id)
        if (controller is not None and controller.runtime_id == address.session.runtime_id
                and address.service_id == self.control.descriptor.service_id):
            # The cache is published only after fsync. Never inspect a live writer's
            # visible-but-uncommitted intent through a separate file descriptor.
            return SessionEndView(address, operation_id, "unknown")
        from .end_evidence import end_state

        return SessionEndView(address, operation_id, end_state(self.owner.project,
                                                               address, operation_id))

    def _publish(self, address, operation_id, state):
        view = SessionEndView(address, operation_id, state)
        with self._lock:
            self._views[address, operation_id] = view
        return view

    def request(self, address, operation_id, connection, proof):
        existing = self.observe(address, operation_id)
        if existing.state != "unknown":
            return existing
        try:
            return self._begin(address, operation_id, connection, proof)
        except ControlRefused:
            # A concurrent identical intent can win the gate and close the
            # runtime. Return its original result, never a false refusal.
            existing = self.observe(address, operation_id)
            if existing.state == "unknown":
                raise
            return existing

    def _begin(self, address, operation_id, connection, proof):
        with self.control.guard(address) as lease:
            self.control.require(lease, connection, proof)
            controller = self.owner.controllers[address.session.session_id]
            with self._lock:
                # Keep outstanding results; completed history is a bounded cache.
                while len(self._views) >= 256:
                    key = next((key for key, row in self._views.items()
                                if row.state == "ended"), None)
                    if key is None:
                        raise ValueError("会话结束记录已满，请先核对未决结果")
                    del self._views[key]
            with controller._lock:
                self.control.require(lease, connection, proof)
                controller.journal.append_session("session.end_requested", dict(
                    address=address.record(), operation_id=operation_id), controller.current)
                controller.request_close()
            view = self._publish(address, operation_id, "closing")
            thread = threading.Thread(target=self._finish, args=(controller, address, operation_id),
                                      name="copilot-session-end", daemon=True)
            with self._lock:
                self._threads.add(thread)
            try:
                thread.start()
            except RuntimeError:
                with self._lock:
                    self._threads.discard(thread)
                self._publish(address, operation_id, "needs_reconcile")
                raise RuntimeError("结束会话已记录，清理尚未完成，请核对原请求") from None
            return view

    def _finish(self, controller, address, operation_id):
        task = CleanupTask(lambda: self._drain(controller, address, operation_id),
                           "copilot-session-drain").start()
        try:
            if not task.wait(self.timeout):
                with self._lock:
                    if self._views[address, operation_id].state == "closing":
                        self._views[address, operation_id] = SessionEndView(
                            address, operation_id, "needs_reconcile")
                # Keep the one original cleanup running. No second close, no
                # closed writer beneath a late handler and no automatic replay.
                task.wait(None)
        except Exception:
            self._publish(address, operation_id, "needs_reconcile")
        finally:
            with self._lock:
                self._threads.discard(threading.current_thread())

    def _drain(self, controller, address, operation_id):
        try:
            self.owner.stop_observation(controller)
            controller.close()
            if not controller.inbox.sealed:
                raise RuntimeError("Pending input shutdown was not durably recorded")
            thread = controller._thread
            if thread is not None:
                thread.join()
            close = getattr(controller.loop, "close", None)
            if close is not None:
                close()
            # Late tool handlers may still append their original receipts. Keep
            # the writer and service alive until those handlers have returned.
            changed = getattr(controller.loop, "_tools_changed", None)
            if changed is not None:
                with changed:
                    changed.wait_for(lambda: not controller.loop._inflight)
            with controller._lock:
                # Execution has drained. Unknown external outcomes remain in the
                # task journal; they do not keep a released runtime alive.
                event = controller.journal.append_session("session.end_observed", dict(
                    address=address.record(), operation_id=operation_id, state="drained"),
                    controller.current)
            self.owner.retire_session(controller, released=lambda: save_release(
                controller.journal.directory, address, operation_id, event["sequence"]))
            self._retired(controller.session_id)
            state = "ended"
            self._publish(address, operation_id, state)
        except Exception:
            # A cleanup error is never proof that execution or external work ended.
            self._publish(address, operation_id, "needs_reconcile")

    def wait(self, timeout=SESSION_END_SECONDS):
        deadline = None if timeout is None else Deadline(timeout)
        with self._lock:
            threads = tuple(self._threads)
        for thread in threads:
            thread.join(None if deadline is None else deadline.remaining())
        return not any(thread.is_alive() for thread in threads)
