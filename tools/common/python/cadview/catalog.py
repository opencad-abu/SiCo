"""Public catalog providers and compatibility exports.

Filesystem catalogs are previews; dbAccess catalogs are authoritative. The
provider boundary never silently replaces an unavailable protected worker."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
from threading import Event
from typing import Any, Callable, Mapping
from cadenv import detach_cadence_mps_environment
from .catalog_errors import CatalogError, CatalogCancelled, CatalogTimeout
from .catalog_model import (
    CATALOG_SCHEMA_VERSION, Catalog, CatalogCategory, CatalogCell, CatalogCombineGroup,
    CatalogLibrary, CatalogSnapshot, CatalogView, CellInfo, CategoryInfo,
    CombineGroupInfo, LibraryInfo, ViewInfo,
)
from .catalog_filesystem import filesystem_catalog
from .catalog_enrichment import _augment_category_metadata, _augment_combine_metadata
from .catalog_process import CatalogOutputCallback, _run_catalog_process
from .catalog_worker import catalog_worker_script

__all__ = [
    "CATALOG_SCHEMA_VERSION", "Catalog", "CatalogCancelled", "CatalogCategory",
    "CatalogCell", "CatalogCombineGroup", "CatalogError", "CatalogLibrary",
    "CatalogOutputCallback", "CatalogSnapshot", "CatalogTimeout", "CatalogView",
    "CellInfo", "CategoryInfo", "CombineGroupInfo", "LibraryInfo", "ViewInfo",
    "filesystem_catalog", "build_filesystem_catalog", "load_filesystem_catalog",
    "dbaccess_catalog", "load_dbaccess_catalog", "load_catalog",
]


# Compatibility exports: supported hosts may keep importing values/providers
# here. Remove helper aliases after those hosts use the owning modules.
def dbaccess_catalog(
    cds_library_file: str | Path,
    *,
    executable: str = "dbAccess",
    script: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    timeout: float = 30.0,
    cancel_event: Event | None = None,
    output_callback: CatalogOutputCallback | None = None,
    process_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> Catalog:
    """Request an authoritative catalog through the dbAccess protocol.

    The default runner owns a short-lived process. ``process_runner`` lets a
    caller with its own serialized source runtime execute the same script;
    that caller owns process isolation, timeout and cancellation cleanup.

    ``script`` may point to a project-specific SKILL script.  When omitted,
    the bundled protocol script writes the protocol object to a task-private
    JSON file.  This keeps PDK ``libInit`` diagnostics on stdout from
    corrupting the protocol.  Custom scripts may continue to emit one JSON
    object on stdout; stderr is reserved for diagnostics and is included in
    errors only when the command fails.  ``output_callback`` receives
    stdout/stderr incrementally while the process is running. It must return
    quickly without blocking; callback failures are advisory and never change
    the provider result.
    """

    cds_path = Path(cds_library_file).expanduser().resolve()
    temporary_script: Path | None = None
    protocol_file: Path | None = None
    child_environment = dict(os.environ if environ is None else environ)
    detach_cadence_mps_environment(child_environment)
    if script is None:
        descriptor, script_name = tempfile.mkstemp(
            prefix="cadview-catalog-", suffix=".il"
        )
        os.close(descriptor)
        temporary_script = Path(script_name)
        descriptor, protocol_name = tempfile.mkstemp(
            prefix="cadview-catalog-", suffix=".json"
        )
        os.close(descriptor)
        protocol_file = Path(protocol_name)
        protocol_file.write_text("", encoding="utf-8")
        child_environment["CADVIEW_CATALOG_OUTPUT"] = str(protocol_file)
        temporary_script.write_text(
            catalog_worker_script(), encoding="utf-8"
        )
        script_path = temporary_script
    else:
        script_path = Path(script).expanduser().resolve()
        if not script_path.is_file():
            raise CatalogError(f"dbAccess catalog script does not exist: {script_path}")

    command = [
        executable,
        "-cdslib",
        str(cds_path),
        "-load",
        str(script_path),
    ]
    try:
        completed = (process_runner or _run_catalog_process)(
            command,
            child_environment,
            timeout=timeout,
            cancel_event=cancel_event,
            output_callback=output_callback,
        )
        if completed.returncode != 0:
            detail = next(
                (line.strip() for line in completed.stderr.splitlines() if line.strip()),
                f"exit status {completed.returncode}",
            )
            raise CatalogError(f"dbAccess catalog provider failed: {detail}")
        try:
            # The bundled SKILL writer uses the private file, but accepting
            # stdout when that file is still empty keeps the provider
            # boundary compatible with older/custom catalog scripts.  Do not
            # silently prefer an empty protocol file over valid stdout.
            protocol_text = ""
            if protocol_file is not None and protocol_file.is_file():
                protocol_text = protocol_file.read_text(
                    encoding="utf-8", errors="replace"
                )
            if protocol_text.strip():
                payload = json.loads(protocol_text)
            else:
                payload = _extract_catalog_json(completed.stdout)
        except json.JSONDecodeError as exc:
            raise CatalogError(
                f"dbAccess catalog provider returned invalid JSON: {exc.msg}"
            ) from exc
        catalog = Catalog.from_dict(
            payload,
            cds_library_file=cds_path,
            authoritative=True,
            provider="dbAccess",
        )
        catalog = _augment_combine_metadata(
            catalog,
            cds_path,
            child_environment,
        )
        return _augment_category_metadata(catalog)
    finally:
        # Both files are task-private.  Clean them up for every exit path,
        # including process cancellation, timeout, and provider failure.
        if temporary_script is not None:
            temporary_script.unlink(missing_ok=True)
        if protocol_file is not None:
            protocol_file.unlink(missing_ok=True)


def _extract_catalog_json(output: str) -> Mapping[str, Any]:
    """Extract the one protocol object from Cadence's noisy stdout.

    dbAccess may print license checkout and PDK ``libInit`` diagnostics to
    stdout before/inside the JSON stream.  A JSON decoder with ``raw_decode``
    lets us ignore that diagnostic text while still requiring one complete,
    versioned object.  We reject multiple protocol objects to avoid accepting
    stale output from a reused process.
    """

    decoder = json.JSONDecoder()
    candidates: list[Mapping[str, Any]] = []
    offset = 0
    while True:
        start = output.find("{", offset)
        if start < 0:
            break
        try:
            value, end = decoder.raw_decode(output, start)
        except json.JSONDecodeError:
            offset = start + 1
            continue
        offset = end
        if isinstance(value, Mapping) and value.get("schema_version") in {1, "1"} and "libraries" in value:
            candidates.append(value)
    if len(candidates) != 1:
        if not candidates:
            raise CatalogError("dbAccess catalog provider returned invalid JSON: no protocol object")
        raise CatalogError("dbAccess catalog provider returned multiple protocol JSON objects")
    return candidates[0]


def load_catalog(
    cds_library_file: str | Path,
    *,
    dbaccess: str | None = None,
    dbaccess_script: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    timeout: float = 30.0,
    cancel_event: Event | None = None,
    output_callback: CatalogOutputCallback | None = None,
) -> Catalog:
    """Load an authoritative catalog when requested, otherwise use fallback."""

    if dbaccess is None:
        return filesystem_catalog(cds_library_file, environ=environ)
    return dbaccess_catalog(
        cds_library_file,
        executable=dbaccess,
        script=dbaccess_script,
        environ=environ,
        timeout=timeout,
        cancel_event=cancel_event,
        output_callback=output_callback,
    )


build_filesystem_catalog = filesystem_catalog
load_filesystem_catalog = filesystem_catalog
load_dbaccess_catalog = dbaccess_catalog
