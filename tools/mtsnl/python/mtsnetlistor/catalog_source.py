"""Validate and fingerprint one source catalog request before cache dispatch."""

from __future__ import annotations

from functools import partial
import math
import os
from pathlib import Path
from threading import Event
import time
from typing import Callable, Mapping, Optional

from .catalog_cache import resolve_source_catalog
from .catalog_fingerprint import _source_cache_key
from .catalog_result import CatalogResult
from .catalog_source_provider import require_source_provider, source_catalog_provider
from .cdslib_fingerprint import _source_cdslib_fingerprint_details
from .environment import isolated_environment
from .errors import RequestValidationError

_SOURCE_CATALOG_CACHE_TTL = 300.0


def load_source_catalog(
    cds_library_file: str | Path,
    *,
    dbaccess: Optional[str] = "dbAccess",
    dbaccess_script: Optional[str | Path] = None,
    environment: Optional[Mapping[str, str]] = None,
    workdir: Optional[str | Path] = None,
    forbidden_target_cds_lib: Optional[str | Path] = None,
    timeout: float = 30.0,
    cancel_event: Optional[Event] = None,
    allow_fallback: bool = False,
    cache_ttl: float | None = None,
    force_refresh: bool = False,
    output_callback: Callable[[str], object] | None = None,
    _shared_cancel_retry: bool = True,
) -> CatalogResult:
    """Load source OA metadata without ever using the host Virtuoso process.

    Successful source results are reused only within this Python process.  The
    recursive ``cds.lib`` fingerprint and detached environment digest are
    calculated before starting Cadence, so a cache hit avoids both a remote
    scheduler wait and PDK initialization.  ``force_refresh`` and
    :func:`clear_source_catalog_cache` are available when an OA change must be
    observed immediately.  Target catalog loading intentionally does not call
    this cache.
    """

    require_source_provider()
    source = Path(cds_library_file).expanduser().resolve()
    if not source.is_file():
        raise RequestValidationError(f"cannot access source cds.lib: {source}")
    try:
        timeout = float(timeout)
    except (TypeError, ValueError) as exc:
        raise ValueError("source catalog timeout must be a finite positive number") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("source catalog timeout must be a finite positive number")
    if cache_ttl is not None:
        try:
            cache_ttl = float(cache_ttl)
        except (TypeError, ValueError) as exc:
            raise ValueError("source catalog cache_ttl must be a finite number") from exc
        if not math.isfinite(cache_ttl) or cache_ttl < 0:
            raise ValueError("source catalog cache_ttl must be a finite non-negative number")
    request_started = time.perf_counter()

    # Validate and normalize the source-only child boundary before consulting
    # the cache.  This keeps a cache hit subject to the same target-leak checks
    # as a cold request, without creating a transient overlay file.
    forbidden_target = (
        None
        if forbidden_target_cds_lib is None
        else Path(forbidden_target_cds_lib).expanduser().resolve()
    )
    base_environment = dict(os.environ if environment is None else environment)
    key_environment = isolated_environment(
        base_environment,
        cds_lib=source,
        workdir=source.parent,
        forbidden_cds_lib=forbidden_target,
    )
    fingerprint_started = time.perf_counter()
    cds_fingerprint, fingerprint_complete = _source_cdslib_fingerprint_details(
        source, key_environment
    )
    fingerprint_ms = (time.perf_counter() - fingerprint_started) * 1000.0
    cache_key = _source_cache_key(
        source,
        cds_fingerprint=cds_fingerprint,
        dbaccess=dbaccess,
        dbaccess_script=dbaccess_script,
        allow_fallback=allow_fallback,
        forbidden_target_cds_lib=forbidden_target,
        executable_environment=key_environment,
    )
    ttl = _SOURCE_CATALOG_CACHE_TTL if cache_ttl is None else cache_ttl

    def retry(shared_cancel_retry: bool) -> CatalogResult:
        return load_source_catalog(
            cds_library_file,
            dbaccess=dbaccess,
            dbaccess_script=dbaccess_script,
            environment=environment,
            workdir=workdir,
            forbidden_target_cds_lib=forbidden_target_cds_lib,
            timeout=timeout,
            cancel_event=cancel_event,
            allow_fallback=allow_fallback,
            cache_ttl=cache_ttl,
            force_refresh=True,
            output_callback=output_callback,
            _shared_cancel_retry=shared_cancel_retry,
        )

    provider = partial(
        source_catalog_provider,
        source,
        dbaccess=dbaccess,
        dbaccess_script=dbaccess_script,
        environment=base_environment,
        workdir=workdir,
        forbidden_target=forbidden_target,
        timeout=timeout,
        cancel_event=cancel_event,
        allow_fallback=allow_fallback,
    )
    return resolve_source_catalog(
        cache_key,
        ttl=ttl,
        force_refresh=force_refresh,
        timeout=timeout,
        cancel_event=cancel_event,
        output_callback=output_callback,
        fingerprint_complete=fingerprint_complete,
        fingerprint_ms=fingerprint_ms,
        request_started=request_started,
        provider=lambda emit: provider(output_callback=emit),
        retry=retry,
        shared_cancel_retry=_shared_cancel_retry,
    )
