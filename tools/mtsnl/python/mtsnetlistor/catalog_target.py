"""Query and validate libraries against the host session target snapshot."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
from threading import Event
from typing import Mapping, Optional
from .catalog_backend import CatalogLibrary, dbaccess_catalog, filesystem_catalog
from .catalog_result import CatalogResult, _cadview_catalog_import_error
from .environment import SessionDescriptor, isolated_environment, write_cds_lib_overlay
from .errors import CatalogError, IsolationError, RequestValidationError


def load_target_catalog(
    session: SessionDescriptor,
    *,
    dbaccess: Optional[str] = "dbAccess",
    dbaccess_script: Optional[str | Path] = None,
    environment: Optional[Mapping[str, str]] = None,
    workdir: Optional[str | Path] = None,
    timeout: float = 30.0,
    cancel_event: Optional[Event] = None,
) -> CatalogResult:
    """Load only libraries available in the current project session."""

    validated = session.validate()
    temporary = None
    if workdir is None:
        temporary = tempfile.TemporaryDirectory(prefix="mts-netlistor-target-catalog-")
        root = Path(temporary.name).resolve()
    else:
        root = Path(workdir).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
    try:
        target_overlay = write_cds_lib_overlay(
            validated.target_cds_lib,
            root / "target-overlay.cds.lib",
        )
        child_environment = isolated_environment(
            environment,
            cds_lib=target_overlay,
            workdir=root,
        )
        if dbaccess_catalog is None:
            raise _cadview_catalog_import_error()
        if not dbaccess:
            if filesystem_catalog is None:
                raise _cadview_catalog_import_error()
            catalog = filesystem_catalog(validated.target_cds_lib, environ=child_environment)
            return CatalogResult(catalog, False, "filesystem", ("target catalog fallback is non-authoritative",))
        try:
            catalog = dbaccess_catalog(
                target_overlay,
                executable=dbaccess,
                script=dbaccess_script,
                environ=child_environment,
                timeout=timeout,
                cancel_event=cancel_event,
            )
        except Exception as exc:
            raise CatalogError(f"authoritative target catalog failed: {exc}") from exc
        # Session snapshot is authoritative for target paths.  Reject any DD/OA
        # result whose physical mapping changed behind the host session's back.
        for library in catalog.libraries:
            expected = validated.target_library_paths.get(library.name)
            if expected is None:
                continue
            if Path(expected).resolve() != library.read_path.resolve():
                raise IsolationError(
                    f"target library {library.name!r} path changed: "
                    f"session={Path(expected).resolve()} catalog={library.read_path.resolve()}"
                )
        # target_overlay is task-private and cleaned below; expose the
        # session-bound target cds.lib in the immutable result instead.
        catalog = replace(catalog, cds_library_file=validated.target_cds_lib)
        return CatalogResult(catalog, True, "dbAccess")
    finally:
        if temporary is not None:
            temporary.cleanup()


def target_library_or_error(result: CatalogResult, name: str) -> CatalogLibrary:
    if not result.authoritative:
        raise IsolationError("target publication requires an authoritative catalog")
    library = result.catalog.library(name)
    if library is None:
        raise RequestValidationError(f"target library is not in the current session cds.lib: {name}")
    if not library.writable or library.write_path is None:
        raise RequestValidationError(f"target library is not writable: {name}")
    return library
