"""Poll idle session metadata away from Qt and from the task RPC consumer."""

from __future__ import annotations

import logging
import threading
import time


class MetadataService:
    def __init__(self, controllers, *, interval=3, status_interval=0.2, observed=None):
        self._controllers = controllers
        self._interval = interval
        self._status_interval = status_interval
        self._observed = observed
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="copilot-metadata", daemon=True)
        self._thread.start()

    def _run(self):
        next_metadata = 0
        next_processes = 0
        while not self._stop.is_set():
            metadata_due = time.monotonic() >= next_metadata
            processes_due = time.monotonic() >= next_processes
            if processes_due:
                next_processes = time.monotonic() + 1
            if metadata_due:
                next_metadata = time.monotonic() + self._interval
            for controller in self._controllers():
                if self._stop.is_set():
                    break
                if processes_due:
                    try:
                        from cadai.process_monitor import snapshot

                        snapshot(str(controller.journal.directory))
                    except Exception:
                        logging.getLogger(__name__).exception("Process observation failed")
                try:
                    controller.poll_router_status()
                except Exception:
                    logging.getLogger(__name__).exception("Session router polling failed")
                try:
                    controller.poll_binding_events()
                    if self._observed is not None:
                        self._observed(controller)
                except Exception:
                    logging.getLogger(__name__).exception("Session binding polling failed")
                if metadata_due:
                    try:
                        controller.poll_metadata()
                    except Exception:
                        logging.getLogger(__name__).exception("Session metadata polling failed")
            self._stop.wait(min(self._status_interval,
                                max(0, next_metadata - time.monotonic())))

    def close(self):
        self._stop.set()

    def wait(self, timeout=None):
        if self._stop.is_set():
            self._thread.join(timeout)
            return not self._thread.is_alive()
        return True


def format_result_json(value):
    """Display serialization stays outside Qt widgets."""
    import json

    return json.dumps(value, ensure_ascii=False, indent=2)


def native_record_title(native):
    from ..storage.native_origin import native_title

    return native_title(native)
