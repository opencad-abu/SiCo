"""Own the detached source catalog provider and its temporary overlay lifetime."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import tempfile
import time
from threading import Event
from typing import Callable, Iterator, Mapping

from .catalog_backend import dbaccess_catalog, filesystem_catalog
from .catalog_result import CatalogResult, _cadview_catalog_import_error
from .environment import isolated_environment, write_cds_lib_overlay
from .errors import CatalogError


def require_source_provider() -> None:
    if dbaccess_catalog is None or filesystem_catalog is None:
        raise _cadview_catalog_import_error()


@contextmanager
def source_catalog_provider(
    source: Path,
    *,
    dbaccess: str | None,
    dbaccess_script: str | Path | None,
    environment: Mapping[str, str],
    workdir: str | Path | None,
    forbidden_target: Path | None,
    timeout: float,
    cancel_event: Event | None,
    allow_fallback: bool,
    output_callback: Callable[[str], object],
) -> Iterator[tuple[CatalogResult, float]]:
    """Yield provider evidence before cleanup so refresh callers share its flight.

    A cleanup error invalidates a successful result. If the provider already
    failed, retain that primary error while still attempting directory cleanup.
    """
    temporary = None
    failed = False
    try:
        if workdir is None:
            temporary = tempfile.TemporaryDirectory(prefix="mts-netlistor-catalog-")
            root = Path(temporary.name).resolve()
        else:
            root = Path(workdir).expanduser().resolve()
            root.mkdir(parents=True, exist_ok=True)
        source_overlay = write_cds_lib_overlay(
            source,
            root / "source-overlay.cds.lib",
            forbidden_paths=(() if forbidden_target is None else (forbidden_target,)),
        )
        child_environment = isolated_environment(
            environment,
            cds_lib=source_overlay,
            workdir=root,
            forbidden_cds_lib=forbidden_target,
        )
        provider_started = time.perf_counter()
        diagnostic = ""
        if dbaccess:
            from .project_worker import active_project_worker
            project_worker = active_project_worker()
            output_callback(
                "Reading source catalog in the project OCEAN runtime.\n"
                if project_worker is not None else
                "Starting detached source dbAccess; PDK and third-party "
                "interface initialization may take time.\n"
            )
            try:
                runner_options = {} if project_worker is None else {"process_runner": project_worker.run_catalog}
                catalog = dbaccess_catalog(
                    source_overlay,
                    executable=dbaccess,
                    script=dbaccess_script,
                    environ=child_environment,
                    timeout=timeout,
                    cancel_event=cancel_event,
                    output_callback=output_callback,
                    **runner_options,
                )
                authoritative = True
            except Exception as exc:
                if cancel_event is not None and cancel_event.is_set():
                    raise CatalogError("source catalog request canceled") from exc
                if not allow_fallback:
                    raise CatalogError(f"authoritative source catalog failed: {exc}") from exc
                diagnostic = f"authoritative source catalog unavailable: {exc}"
                catalog = filesystem_catalog(source, environ=child_environment)
                authoritative = False
        else:
            diagnostic = "authoritative dbAccess provider disabled"
            catalog = filesystem_catalog(source, environ=child_environment)
            authoritative = False
        if cancel_event is not None and cancel_event.is_set():
            raise CatalogError("source catalog request canceled")
        provider_seconds = time.perf_counter() - provider_started
        # Never return the private overlay that cleanup removes.
        catalog = replace(catalog, cds_library_file=source)
        yield CatalogResult(
            catalog, authoritative, "dbAccess" if authoritative else "filesystem",
            (() if not diagnostic else (diagnostic,)),
        ), provider_seconds
    except BaseException:
        failed = True
        raise
    finally:
        if temporary is not None:
            try:
                temporary.cleanup()
            except BaseException:
                if not failed:
                    raise
