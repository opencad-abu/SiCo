"""Latest-request state and serialized worker lifecycle for MTS controllers."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from threading import Event, Lock
from typing import Callable

from ..catalog import CatalogResult
from ..defaults import DefaultsReport, SourceDefaults
from ..environment import SessionDescriptor
from ..errors import MtsNetlistorError
from ..publish import PublicationBundleResult
from ..workflow import GenerationResult, MultiGenerationResult


@dataclass(frozen=True)
class ControllerState:
    token: int = 0
    stage: str = "idle"
    busy: bool = False
    source_catalog: CatalogResult | None = None
    target_catalog: CatalogResult | None = None
    generation: GenerationResult | MultiGenerationResult | None = None
    defaults: DefaultsReport | SourceDefaults | None = None
    publication: PublicationBundleResult | tuple[PublicationBundleResult, ...] | None = None
    error: str = ""
    diagnostics: tuple[str, ...] = ()


StateCallback = Callable[[ControllerState], object]
LogCallback = Callable[[str], object]
CATALOG_LOG_PREFIX = "\x00CATALOG\x00"


class ControllerTasks:
    def __init__(
        self,
        *,
        session: SessionDescriptor | None = None,
        max_workers: int = 2,
        on_state: StateCallback | None = None,
        on_log: LogCallback | None = None,
        persistent_source: bool = False,
    ) -> None:
        self.session = session
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="mts-netlistor")
        self._lock = Lock()
        # Superseding a request cancels it immediately, but its subprocess
        # teardown must finish before the next request enters Cadence.
        self._worker_lock = Lock()
        self._closed = False
        self._state = ControllerState()
        self._token = 0
        self._future: Future[object] | None = None
        self._cancel: Event | None = None
        # Tokens recorded here were explicitly canceled by the caller.  A
        # worker callback may arrive after cancel() advances the public token;
        # this lets us retain a late publication evidence result without
        # accepting an unrelated stale callback.
        self._canceled_tokens: set[int] = set()
        self._on_state = on_state
        self._on_log = on_log
        from ..project_worker import ProjectWorker
        self._project_worker = ProjectWorker() if persistent_source else None
        self._source_epoch = 0
        self._project_epoch = 0
        self._project_close_future = None

    @property
    def state(self) -> ControllerState:
        with self._lock:
            return self._state

    def set_session(self, session: SessionDescriptor | None) -> None:
        with self._lock:
            self.session = session

    def close(self, wait: bool = True) -> None:
        with self._lock:
            first_close = not self._closed
            self._closed = True
        self.cancel()
        if self._project_worker is not None and first_close:
            # Queue shutdown behind in-flight cancellation, even when a page
            # asks for asynchronous close. Do not cancel this cleanup future.
            self._project_close_future = self._executor.submit(self._close_project_worker)
        self._executor.shutdown(wait=wait, cancel_futures=False)
        if wait and self._project_close_future is not None:
            self._project_close_future.result()

    def _close_project_worker(self, epoch=None) -> None:
        with self._worker_lock:
            # A newer operation may acquire the lock before queued reset
            # cleanup. Never let that stale cleanup retire the new runtime.
            if epoch is not None and self._project_epoch >= epoch:
                return
            if self._project_worker is not None:
                self._project_worker.close()
            if epoch is not None:
                self._project_epoch = epoch

    def reset_source_project(self) -> None:
        self.cancel()
        with self._lock:
            self._source_epoch += 1
            if self._project_worker is not None and not self._closed:
                self._executor.submit(self._close_project_worker, self._source_epoch)

    def _source_call(self, function, argument, bound_environment, **kwargs):
        if self._project_worker is None:
            return function(argument, **kwargs)
        if self._project_epoch != self._source_epoch:
            self._project_worker.close()
            self._project_epoch = self._source_epoch
        with self._project_worker.scope(getattr(argument, "source", argument), bound_environment):
            result = function(argument, **kwargs)
            if isinstance(result, CatalogResult) and result.authoritative:
                self._project_worker.warm(
                    timeout=kwargs.get("timeout", 180.0),
                    cancel=kwargs.get("cancel_event"),
                    output_callback=kwargs.get("output_callback"),
                )
            return result

    def cancel(self) -> None:
        with self._lock:
            active_token = self._token
            if self._cancel is not None:
                self._cancel.set()
                self._canceled_tokens.add(active_token)
            # Drop references to the canceled task immediately.  The worker
            # still owns its Event/Future through the completion callback, but
            # a later operation must not mistake either object for the active
            # task.
            self._cancel = None
            self._future = None
            self._token += 1
            token = self._token
            current = self._state
            self._state = replace(
                current,
                token=token,
                stage="canceled",
                busy=False,
            )
            state = self._state
        self._emit(state)

    def _emit(self, state: ControllerState) -> None:
        callback = self._on_state
        if callback is not None and not self._closed:
            try:
                callback(state)
            except Exception:
                # A closing Qt receiver cannot prevent cancellation/cleanup.
                pass

    def _emit_log(self, token: int, message: str) -> None:
        """Forward diagnostics only while their operation is still current."""

        # A killed OCEAN process can flush a final pipe/transcript chunk after
        # ``cancel()`` has advanced the controller token.  Dropping that tail
        # prevents an old run from being shown as part of a newer operation.
        with self._lock:
            if token != self._token:
                return

        callback = self._on_log
        if callback is None or not message:
            return
        try:
            callback(message)
        except BaseException:
            # Logging is advisory.  A GUI being closed or a consumer raising
            # must never turn a successful OCEAN run into a failed operation.
            return

    def _begin(self, stage: str) -> tuple[int, Event]:
        with self._lock:
            if self._closed:
                raise MtsNetlistorError("controller is closed")
            if self._cancel is not None:
                self._cancel.set()
            self._token += 1
            token = self._token
            cancel = Event()
            self._cancel = cancel
            # A new asynchronous operation must not erase catalogs or the
            # last successful generation.  Those values are still useful to
            # the GUI while the next request is being prepared, and keeping
            # them also prevents an old catalog result from appearing to
            # disappear merely because a generation was started.
            self._state = replace(
                self._state,
                token=token,
                stage=stage,
                busy=True,
                error="",
                defaults=None,
                # Publication diagnostics belong to the operation that
                # produced them.  Do not replay an old result on every state
                # update for a new operation.
                publication=None,
            )
            state = self._state
        self._emit(state)
        return token, cancel

    def _finish(self, token: int, *, stage: str, **values: object) -> None:
        with self._lock:
            if token != self._token:
                # ``cancel()`` advances the token before the child process can
                # necessarily finish.  A publication wrapper may still return
                # a conservative ``manual_cleanup_required`` result after OA
                # mutation has started.  Preserve that evidence only when the
                # canceled operation is still the current terminal state; a
                # newer operation must never be overwritten by a late result.
                explicitly_canceled = token in self._canceled_tokens
                self._canceled_tokens.discard(token)
                publication = values.get("publication")
                if (
                    stage == "publication_ready"
                    and publication is not None
                    and explicitly_canceled
                    and self._state.stage == "canceled"
                ):
                    self._state = replace(self._state, publication=publication)
                    state = self._state
                else:
                    return
            else:
                self._state = replace(self._state, token=token, stage=stage, busy=False, **values)
                # Terminal callbacks release the active-task references.  The
                # callback closure retains its own values until it returns.
                self._future = None
                self._cancel = None
                state = self._state
        self._emit(state)

    def _fail(self, token: int, stage: str, exc: BaseException) -> None:
        self._finish(token, stage=stage, error=f"{type(exc).__name__}: {exc}")

    def _submit(self, token: int, cancel: Event, worker: Callable[[], object], done: Callable[[object], None], stage: str) -> int:
        def run() -> object:
            with self._worker_lock:
                if cancel.is_set():
                    raise MtsNetlistorError("operation canceled")
                return worker()

        future = self._executor.submit(run)
        with self._lock:
            if token == self._token:
                self._future = future

        def completed(item: Future[object]) -> None:
            try:
                result = item.result()
            except BaseException as exc:
                if cancel.is_set():
                    self._finish(token, stage="canceled")
                else:
                    self._fail(token, stage, exc)
                return
            if cancel.is_set():
                # Keep publication evidence if the worker reached an OA
                # mutating stage before cancellation was observed.  Generation
                # and catalog results remain safely discardable.
                if stage == "publishing" and (
                    isinstance(result, PublicationBundleResult)
                    or (
                        isinstance(result, tuple)
                        and all(isinstance(item, PublicationBundleResult) for item in result)
                    )
                ):
                    self._finish(
                        token,
                        stage="publication_ready",
                        publication=result,
                    )
                else:
                    self._finish(token, stage="canceled")
                return
            try:
                done(result)
            except BaseException as exc:
                self._fail(token, stage, exc)

        future.add_done_callback(completed)
        return token
