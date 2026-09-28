"""Verified selector controls for applying an LSF snapshot to a flow."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PyQt5.QtWidgets import QHBoxLayout, QPushButton

from cadgui.protocol import PROTOCOL_VERSION, write_transfer

from .selection_state import SelectionState


class SelectorActions:
    def __init__(
        self,
        output: Path | None,
        *,
        read_selection: Callable[[], SelectionState],
        show_status: Callable[[str, str, str], None],
        close: Callable[[], object],
        validation_controller=None,
    ) -> None:
        self.output = output
        self._read_selection = read_selection
        self._show_status = show_status
        self._close = close
        if output is not None and validation_controller is None:
            raise ValueError("Selector output requires a validation controller")
        self.validation_controller = validation_controller
        self._pending_fingerprint: tuple[str, str | None] | None = None
        self.use_auto_button: QPushButton | None = None
        self.use_host_button: QPushButton | None = None
        self.cancel_button: QPushButton | None = None

    def add_to_layout(self, outer, parent) -> None:
        if self.output is None:
            return
        actions = QHBoxLayout()
        actions.setSpacing(6)
        actions.addStretch(1)
        self.use_auto_button = QPushButton("Use Auto", parent)
        self.use_auto_button.setObjectName("useAutoButton")
        self.use_auto_button.setToolTip(
            "Use the selected queue and let the LSF scheduler choose a host"
        )
        self.use_host_button = QPushButton("Use Selected Host", parent)
        self.use_host_button.setObjectName("useSelectedHostButton")
        self.use_host_button.setToolTip("Use the selected queue and execution host")
        self.cancel_button = QPushButton("Cancel", parent)
        self.cancel_button.setObjectName("cancelButton")
        actions.addWidget(self.use_auto_button)
        actions.addWidget(self.use_host_button)
        actions.addWidget(self.cancel_button)
        outer.addLayout(actions)

    def connect(self) -> None:
        if self.output is None:
            return
        assert self.use_auto_button is not None
        assert self.use_host_button is not None
        assert self.cancel_button is not None
        self.use_auto_button.clicked.connect(self.use_auto)
        self.use_host_button.clicked.connect(self.use_selected_host)
        self.cancel_button.clicked.connect(self._close)
        assert self.validation_controller is not None
        self.validation_controller.busyChanged.connect(self.sync)
        self.validation_controller.succeeded.connect(self._validated)
        self.validation_controller.failed.connect(self._validation_failed)
        self.sync()

    def validated_queue(self) -> str | None:
        state = self._read_selection()
        return state.queue if state.queue_eligible else None

    def validated_host(self) -> str | None:
        state = self._read_selection()
        return state.host if state.host_eligible else None

    def sync(self, *_args) -> None:
        if self.output is None:
            return
        assert self.use_auto_button is not None
        assert self.use_host_button is not None
        validating = bool(
            self.validation_controller is not None and self.validation_controller.busy
        )
        state = self._read_selection()
        self.use_auto_button.setEnabled(
            state.queue_eligible and not validating and not state.closing
        )
        self.use_host_button.setEnabled(
            state.host_eligible and not validating and not state.closing
        )

    def publish(self, host: str | None) -> None:
        state = self._read_selection()
        queue = state.queue if state.queue_eligible else None
        if queue is None or (
            host is not None and (not state.host_eligible or host != state.host)
        ):
            self._show_status("stale", "Selection needs refresh", "")
            self.sync()
            return
        assert self.validation_controller is not None
        fingerprint = (queue, host)
        self._pending_fingerprint = fingerprint
        self._show_status("refreshing", "Validating LSF selection", "")
        self.sync()
        if not self.validation_controller.validate(queue, host):
            self._pending_fingerprint = None
            self._show_status(
                "error", "Cannot validate selection", "Validation is already running"
            )
            self.sync()
            return
        self.sync()

    def _validated(self, queue: str, host: object) -> None:
        state = self._read_selection()
        if state.closing:
            self._pending_fingerprint = None
            return
        if host is not None and not isinstance(host, str):
            self._pending_fingerprint = None
            self._show_status(
                "error", "Cannot validate selection", "Invalid validation result"
            )
            self.sync()
            return
        resolved_host = host
        fingerprint = (queue, resolved_host)
        pending = self._pending_fingerprint
        self._pending_fingerprint = None
        current = state.fingerprint(resolved_host is not None)
        if pending != fingerprint or current != fingerprint:
            self._show_status(
                "stale",
                "Selection changed during validation",
                "Review the current queue and host, then apply again",
            )
            self.sync()
            return
        records = [("QUEUE", queue)]
        if resolved_host is not None:
            records.append(("HOST", resolved_host))
        try:
            write_transfer(
                self.output,
                records,
                status="applied",
                version=PROTOCOL_VERSION,
                refuse_existing=True,
            )
        except (OSError, TypeError, ValueError) as exc:
            self._show_status("error", "Cannot publish selection", str(exc))
            return
        self._close()

    def _validation_failed(self, message: str) -> None:
        self._pending_fingerprint = None
        if self._read_selection().closing:
            return
        self._show_status("error", "Cannot validate selection", message)
        self.sync()

    def shutdown(self, timeout_ms: int = 2_000) -> bool:
        self._pending_fingerprint = None
        if self.validation_controller is None:
            return True
        return self.validation_controller.shutdown(timeout_ms)

    def use_auto(self, _checked: bool = False) -> None:
        self.publish(None)

    def use_selected_host(self, _checked: bool = False) -> None:
        host = self.validated_host()
        if host is not None:
            self.publish(host)


__all__ = ["SelectorActions"]
