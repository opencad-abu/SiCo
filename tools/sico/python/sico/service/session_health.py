"""Observe the captured host per session, pausing loss without rebinding it."""

from contextlib import nullcontext

from .metadata import MetadataService


class SessionHealth:
    def __init__(self, controller):
        self.controller = controller
        self._reported = None
        self.metadata = MetadataService(lambda: (controller,), observed=self.observe)

    def observe(self, controller):
        if not controller._lock.acquire(blocking=False):
            return
        try:
            if controller.closing or controller._shutdown.is_set():
                return
            cause = self._host_loss(controller)
            if cause is None:
                return  # Recovery never resumes a paused queue implicitly.
            source, upstream_reason = cause
            controller.paused = True
            loop = controller.loop
            if controller.busy:
                with getattr(loop, "lock", nullcontext()):
                    loop.stale = True
                    loop.stale_reason = "捕获的 Virtuoso 来源不可用；请核对原操作，不会自动重绑"
            if cause != self._reported:
                controller.journal.append_session("session.target_unavailable", dict(
                    context=controller.current.record(), reason="captured_host_unavailable",
                    source=source, upstream_reason=upstream_reason), controller.current)
                self._reported = cause
                controller._changed()
        finally:
            controller._lock.release()

    def _host_loss(self, controller):
        """Name the first captured-source loss as (source, upstream_reason)."""
        sync_error = controller.bindings.sync_error
        if sync_error:
            return ("binding_sync_error", sync_error)
        invalidated = (controller.bindings.events.state or {}).get("invalidated", {})
        target_reason = invalidated.get(controller.current.target_id)
        if target_reason:
            return ("target_invalidated", str(target_reason))
        router = controller.router.snapshot(controller.current)
        if router is not None and router["state"] == "router_unavailable":
            return ("router_unavailable", router["state"])
        return None

    def close(self):
        self.metadata.close()

    def wait(self):
        self.metadata.wait()
