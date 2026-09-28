"""Event-worker ownership, bounded replay slots and off-Qt retirement."""

import threading

from .workbench_data import WorkbenchService


class EventService(WorkbenchService):
    def __init__(self):
        self._streams = []
        super().__init__(name="copilot-events")

    def own(self, stream):
        self._idle()
        if len(self._streams) >= 64:
            raise ValueError("Too many active replay preparations")
        self._streams.append(stream)

    def _idle(self):
        kept = []
        for stream in self._streams:
            if stream._closed or self._closing:
                stream.close()
                stream.retire()
            else:
                kept.append(stream)
        self._streams = kept


def preparation_receipt(service, operation, *args):
    abandoned = threading.Event()
    future = service._request(operation, *args)
    # DataReceipt can abandon a running preparation without a blocking wait.
    def finished(receipt):
        if abandoned.is_set() and not receipt.cancelled() and receipt.exception() is None:
            receipt.result().stream.close()

    future.add_done_callback(finished)

    def discard():
        abandoned.set()
        if future.done():
            finished(future)

    future.discard = discard
    return future
