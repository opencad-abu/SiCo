"""Pump Qt until asynchronous desktop commands and their receipts have settled."""

import time

from PyQt5.QtWidgets import QApplication

from sico_ui.receipts import DataReceipt
from agent_startup_fixture import window_backend


def wait_until(condition, timeout=5):
    deadline = time.monotonic() + timeout
    while True:
        QApplication.instance().processEvents()
        if condition():
            return
        assert time.monotonic() < deadline, "Asynchronous UI receipt did not complete"
        time.sleep(0.005)


def receipts(widget):
    def ready():
        for receipt in widget.findChildren(DataReceipt):
            receipt.poll()
        return not any(receipt.pending for receipt in widget.findChildren(DataReceipt))

    wait_until(ready)


def settle(window, *, timeout=5, tasks=True):
    deadline = time.monotonic() + timeout
    backend = window_backend(window)
    while True:
        QApplication.instance().processEvents()
        window.commands.poll()
        window.binding.poll()
        busy = window.api.desktop_busy if tasks or backend is None else backend.worker.busy
        for receipt in window.findChildren(DataReceipt):
            receipt.poll()
        pending_data = any(receipt.pending for receipt in window.findChildren(DataReceipt))
        delivery = getattr(window.binding, "delivery", None)
        events_pending = (delivery is not None and not window.binding.failed
                          and (delivery.future is not None or delivery.batch is not None
                               or not delivery.caught_up))
        # A caught-up batch can precede the worker's final state publication.
        # In-process fixtures own the Controller; compare that version only once
        # tasks are idle, without adding a production backdoor to the window.
        owner = (backend.controllers.get(window.page.session.session_id)
                 if backend is not None and tasks and not busy
                 and window.page.reviewing is None else None)
        snapshot_pending = (owner is not None and not window.binding.failed
                            and not window.lifecycle.closing
                            and window.binding.version < owner.version)
        if (not busy and not window.commands.pending and not window.page.opening
                and not pending_data and not events_pending and not snapshot_pending
                and not window.details.current_transcript().dirty
                and not window.centers._snapshot_needed):
            break
        assert time.monotonic() < deadline, {
            "status": window.status.text(), "busy": busy,
            "commands": bool(window.commands.pending), "opening": window.page.opening,
            "data": pending_data, "events": events_pending,
            "transcript": window.details.current_transcript().dirty,
            "snapshot": window.centers._snapshot_needed,
            "published_state": snapshot_pending,
            "visible": window.isVisible(), "binding_failed": window.binding.failed,
        }
        time.sleep(0.005)
    if backend is not None:
        backend.catalog.request().result(timeout)
    receipts(window)
    QApplication.instance().processEvents()
    window.navigation.refresh()
    window.binding.poll()


def activate(window, session_id):
    window.page.activate_session(session_id)
    settle(window, tasks=False)
