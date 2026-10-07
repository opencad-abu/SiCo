"""Read owner commands and release the control pipe on shutdown."""

from __future__ import annotations

import errno
import os
import sys
from .protocol import ControlDecoder, parse_control_fd


class ControlChannel:
    def __init__(self, parent, show_callback, shutdown_callback, notifier_type) -> None:
        self._parent = parent
        self._show_callback = show_callback
        self._shutdown_callback = shutdown_callback
        self._decoder = ControlDecoder()
        self._fd = parse_control_fd()
        self._notifier = None
        self._shutdown_sent = False
        if self._fd is not None:
            self._notifier = notifier_type(self._fd, notifier_type.Read, parent)
            self._notifier.activated.connect(self._read_available)

    def close(self) -> None:
        if self._notifier is not None:
            self._notifier.setEnabled(False)
            self._notifier.deleteLater()
            self._notifier = None
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    def _request_shutdown(self) -> None:
        if self._shutdown_sent:
            return
        self._shutdown_sent = True
        self.close()
        self._shutdown_callback()

    def _read_available(self, *_arguments) -> None:
        while self._fd is not None:
            try:
                data = os.read(self._fd, 4096)
            except InterruptedError:
                continue
            except BlockingIOError:
                return
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    return
                self._request_shutdown()
                return
            result = self._decoder.feed(data) if data else self._decoder.eof()
            for command in result.commands:
                if command == "show":
                    self._show_callback()
            if result.shutdown:
                if result.oversized:
                    print(
                        "sico-ai-terminal: control command exceeds the size limit",
                        file=sys.stderr,
                    )
                self._request_shutdown()
                return
            if len(data) < 4096:
                return
