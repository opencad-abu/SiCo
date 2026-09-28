"""Run an immutable batch of process capabilities in deterministic order."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Callable
from PyQt5.QtCore import QTimer


@dataclass(frozen=True)
class BatchProcess:
    identity: int
    poll: Callable
    state: Callable
    defaults_busy: Callable
    handoff: Callable
    result: Callable
    run: Callable
    cancel: Callable
    select: Callable
    label: Callable


class ProcessBatch:
    def __init__(self, *, pages, lock_ui, buttons, show_status, parent):
        self._pages = pages
        self._lock_ui = lock_ui
        self._buttons = buttons
        self._show_status = show_status
        self._queue: list[BatchProcess] = []
        self._snapshot: tuple[BatchProcess, ...] = ()
        self.current: BatchProcess | None = None
        self._started = False
        self.active = False
        self._failures: list[str] = []
        self._timer = QTimer(parent)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self.poll)

    @staticmethod
    def _select(page):
        page.select()

    @staticmethod
    def _label(page):
        return page.label()

    def start(self, _signal_value=None) -> None:
        if self.active:
            return
        pages = self._pages()
        if self.active or not pages:
            return
        self._snapshot = tuple(pages)
        self._queue = list(self._snapshot)
        self.current = None
        self._started = False
        self._failures = []
        self.active = True
        self._buttons(True)
        self._lock_ui(True)
        self._show_status("Running process tabs")
        self.next_process()

    def next_process(self) -> None:
        if not self.active:
            return
        if not self._queue:
            self.finish()
            return
        # Defaults/catalog workers can have been started by selecting cells
        # before Run. Wait for every page to settle so the batch itself has a
        # single active source/target worker at a time.
        for page in self._snapshot:
            page.poll()
        if any(
            page.state().busy
            or page.defaults_busy()
            or page.handoff() is not None
            for page in self._snapshot
        ):
            self._timer.start()
            return
        page = self._queue.pop(0)
        self._select(page)
        self.current = page
        self._started = False
        self._timer.start()

    def poll(self) -> None:
        page = self.current
        if not self.active:
            self._timer.stop()
            return
        if page is None:
            self.next_process()
            return
        # The page's own timer normally drains this handoff. Drain it here as
        # well so Run All never races a just-completed catalog/defaults worker.
        page.poll()
        state = page.state()
        if not self._started and state.busy:
            # Initial source/target cataloging and PDK probing must settle
            # before the page request is validated.
            return
        # Selecting a source view starts an isolated PDK defaults probe. Run
        # All waits for that page's queue to drain instead of silently skipping
        # a process whose simulator/model defaults are still being loaded.
        if not self._started:
            if page.defaults_busy():
                return
            self._started = True
            if not page.run():
                self._failures.append(self._label(page))
                self._started = False
                self._timer.stop()
                self.current = None
                QTimer.singleShot(0, self.next_process)
                return
        state = page.state()
        if state.busy or page.handoff() is not None:
            return
        terminal = (
            page.result() is not None
            or bool(state.error)
            or state.stage in {"error", "canceled", "publication_ready"}
        )
        if not terminal:
            return
        if state.error:
            self._failures.append(self._label(page))
        publication = state.publication
        publication_results = (
            publication
            if isinstance(publication, tuple)
            else (() if publication is None else (publication,))
        )
        if any(getattr(result, "status", "") != "succeeded" for result in publication_results):
            self._failures.append(self._label(page))
        self._timer.stop()
        self.current = None
        QTimer.singleShot(0, self.next_process)

    def finish(self) -> None:
        self._timer.stop()
        self.active = False
        self.current = None
        self._started = False
        self._snapshot = ()
        self._buttons(False)
        self._lock_ui(False)
        if self._failures:
            self._show_status("Run All finished with errors: " + ", ".join(self._failures))
        else:
            self._show_status("Run All finished")

    def cancel(self, _signal_value=None) -> None:
        if not self.active:
            return
        self._timer.stop()
        for page in self._snapshot:
            page.cancel()
        self.active = False
        self._queue.clear()
        self._snapshot = ()
        self.current = None
        self._started = False
        self._buttons(False)
        self._lock_ui(False)
        self._show_status("Run All canceled")

    def select_active(self) -> None:
        """Keep tab insertion focused on the batch's current or first process."""
        process = self.current or (self._snapshot[0] if self._snapshot else None)
        if process is not None:
            process.select()
