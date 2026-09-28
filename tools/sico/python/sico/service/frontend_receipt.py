"""Project worker receipts without publishing their internal result objects."""

import threading
from concurrent.futures import CancelledError, Future


def project_receipt(original, project, *, discard=None):
    result = Future()
    abandoned = threading.Event()

    def finished(receipt):
        if not result.set_running_or_notify_cancel():
            return
        try:
            if abandoned.is_set():
                raise CancelledError()
            value = project(receipt.result())
            result.set_result(value)
        except BaseException as exc:
            result.set_exception(exc)

    def abandon():
        abandoned.set()
        cleanup = getattr(original, "discard", None)
        if cleanup is not None:
            cleanup()
        if result.done() and not result.cancelled() and result.exception() is None:
            if discard is not None:
                discard(result.result())

    def cancelled(receipt):
        if receipt.cancelled():
            abandon()
            original.cancel()
        elif abandoned.is_set() and discard is not None and receipt.exception() is None:
            discard(receipt.result())

    result.discard = abandon
    result.add_done_callback(cancelled)
    original.add_done_callback(finished)
    return result
