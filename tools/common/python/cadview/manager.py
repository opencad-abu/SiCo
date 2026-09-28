"""Qt-free orchestration for loading Cadence library catalogs.

LibraryManager owns provider registration, the worker pool, request generations
and their shared lifecycle lock. Public result/request imports remain compatible;
retire those aliases after supported callers migrate to the owning modules.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, RLock
from typing import Callable, Mapping, Optional

from .catalog import (
    Catalog,
    CatalogCancelled,
    CatalogError,
    dbaccess_catalog,
    filesystem_catalog,
)
from .manager_models import CatalogDiagnostics, CatalogResult
from .manager_request import CatalogRequest
from .manager_provider import CatalogLoader, invoke_provider

Provider = str


class LibraryManager:
    """Load source catalogs synchronously or in a bounded worker pool.

    ``provider`` accepts ``"filesystem"`` (preview) or ``"dbAccess"``
    (authoritative).  A custom callable may be supplied through
    ``providers`` for tests or site-specific OA frontends; it receives the
    same keyword arguments as the built-in provider.
    """

    def __init__(
        self,
        *,
        providers: Optional[Mapping[str, CatalogLoader]] = None,
        max_workers: int = 1,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least one")
        builtins: dict[str, CatalogLoader] = {
            "filesystem": filesystem_catalog,
            "dbAccess": dbaccess_catalog,
            "dbaccess": dbaccess_catalog,
        }
        if providers:
            for raw_name, loader in providers.items():
                name = str(raw_name).strip()
                if not name:
                    raise ValueError("catalog provider name must not be empty")
                if not callable(loader):
                    raise TypeError("catalog provider must be callable")
                builtins[name] = loader
        self._providers = builtins
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="cadview-catalog"
        )
        self._lock = RLock()
        self._closed = False
        self._generation = 0
        self._requests: dict[int, CatalogRequest] = {}

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def providers(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._providers))

    def load(
        self,
        cds_library_file: str | Path,
        *,
        provider: Provider | CatalogLoader = "dbAccess",
        executable: str = "dbAccess",
        script: str | Path | None = None,
        environ: Mapping[str, str] | None = None,
        timeout: float = 30.0,
        cancel_event: Event | None = None,
        output_callback: Callable[[str], object] | None = None,
    ) -> CatalogResult:
        """Load one catalog and attach normalized diagnostics.

        This method performs no implicit fallback: callers must explicitly
        request ``filesystem`` when an authoritative dbAccess result is not
        required.  That avoids accidentally enabling destructive publication
        from a directory-only preview.
        """

        with self._lock:
            if self._closed:
                raise RuntimeError("library manager is closed")
        path = Path(cds_library_file).expanduser().resolve()
        if not path.is_file():
            raise CatalogError(f"cannot access cds.lib: {path}")
        if callable(provider):
            name = getattr(provider, "__name__", provider.__class__.__name__)
            loader = provider
        else:
            name = str(provider).strip()
            with self._lock:
                loader = self._providers.get(name)
        if loader is None:
            available = ", ".join(self.providers)
            raise CatalogError(f"unknown catalog provider {name!r}; available: {available}")
        if cancel_event is not None and not isinstance(cancel_event, Event):
            raise TypeError("cancel_event must be a threading.Event")
        event = cancel_event if cancel_event is not None else Event()
        if event.is_set():
            raise CatalogCancelled("catalog request cancelled")
        kwargs: dict[str, object] = {
            "executable": executable,
            "script": script,
            "environ": environ,
            "timeout": timeout,
            "cancel_event": event,
            "output_callback": output_callback,
        }
        loaded = invoke_provider(loader, path, kwargs)
        if isinstance(loaded, CatalogResult):
            if event.is_set():
                raise CatalogCancelled("catalog request cancelled")
            return loaded
        catalog = loaded
        if not isinstance(catalog, Catalog):
            raise CatalogError("catalog provider returned an invalid Catalog")
        if event.is_set():
            raise CatalogCancelled("catalog request cancelled")
        diagnostics = CatalogDiagnostics(
            cds_library_file=path,
            provider=catalog.provider,
            authoritative=catalog.authoritative,
        )
        return CatalogResult(catalog=catalog, diagnostics=diagnostics)

    def submit(self, cds_library_file: str | Path, **kwargs: object) -> CatalogRequest:
        """Submit a latest-result request to the manager's worker pool."""

        event = kwargs.pop("cancel_event", None)

        with self._lock:
            if self._closed:
                raise RuntimeError("library manager is closed")
            if event is not None and not isinstance(event, Event):
                raise TypeError("cancel_event must be a threading.Event")
            cancel_event = event if event is not None else Event()
            # Scheduling and registration are one atomic manager operation.
            # Otherwise cancel()/close(), or a concurrent newer submit(), can
            # run after executor.submit() but before this request is visible.
            future = self._executor.submit(
                self.load,
                cds_library_file,
                cancel_event=cancel_event,
                **kwargs,
            )
            self._generation += 1
            generation = self._generation
            request = CatalogRequest(future, cancel_event)
            self._requests[generation] = request
            future.add_done_callback(
                lambda _future, token=generation, current=request: self._retire_request(
                    token, current
                )
            )
            # A new request supersedes older requests, but cancellation is
            # cooperative because dbAccess may already be running.
            for token, previous in tuple(self._requests.items()):
                if token < generation:
                    self._requests.pop(token, None)
                    # A result that completed before the superseding request
                    # was registered is successful, not retroactively
                    # cancelled.  Otherwise a caller retaining its request
                    # handle would observe ``cancelled`` change after
                    # ``future.result()`` already succeeded.
                    if not previous.done:
                        previous.cancel()
        return request

    def _retire_request(self, generation: int, request: CatalogRequest) -> None:
        """Drop a completed request without affecting newer submissions."""

        with self._lock:
            if self._requests.get(generation) is request:
                self._requests.pop(generation, None)

    def register_provider(self, name: str, loader: CatalogLoader) -> None:
        """Register or replace a site-specific provider before submission."""

        provider_name = str(name).strip()
        if not provider_name:
            raise ValueError("catalog provider name must not be empty")
        if not callable(loader):
            raise TypeError("catalog provider must be callable")
        with self._lock:
            if self._closed:
                raise RuntimeError("library manager is closed")
            self._providers[provider_name] = loader

    def cancel(self) -> None:
        with self._lock:
            requests = tuple(self._requests.values())
            self._requests.clear()
            self._generation += 1
        for request in requests:
            request.cancel()

    def close(self, *, wait: bool = True) -> None:
        with self._lock:
            was_closed = self._closed
            self._closed = True
        if not was_closed:
            self.cancel()
        # ThreadPoolExecutor permits a later wait=True shutdown after a
        # previous wait=False call.  Preserve that useful completion behavior
        # while keeping repeated close() calls harmless.
        self._executor.shutdown(wait=wait, cancel_futures=True)

    def __enter__(self) -> "LibraryManager":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()


__all__ = [
    "CatalogDiagnostics",
    "CatalogRequest",
    "CatalogResult",
    "LibraryManager",
]
