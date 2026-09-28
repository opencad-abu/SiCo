"""Stable environment, executable and protocol identity for source queries."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Mapping


_SOURCE_CATALOG_CACHE_VERSION = "source-catalog-cache.v1"


_BUNDLED_DBACCESS_SCRIPT_VERSION = "cadview-dbaccess-catalog-script.v2"


def _cache_environment_digest(environment: Mapping[str, str]) -> str:
    """Hash the effective source environment without retaining its values."""

    # These selectors are rebuilt by ``isolated_environment`` and include
    # transient run paths or the host Virtuoso MPS identity.  Hashing them
    # would defeat reuse and could expose the host-session boundary through a
    # cache key.  All project/PDK variables, PATH entries, and license choices
    # remain part of the digest.
    ignored = {
        "CDS_LIB",
        "CDS_CDSLIB",
        "MTS_NETLISTOR_CDSLIB",
        "MTS_NETLISTOR_WORKDIR",
        "MTS_NETLISTOR_TARGET_CDSLIB",
        "SICO_TARGET_CDSLIB",
        "CADENCE_TARGET_CDSLIB",
        "MTS_SOURCE_LIB",
        "MTS_SOURCE_CELL",
        "MTS_TARGET_LIB",
        "MTS_TARGET_CELL",
        "MTS_TARGET_ROOT",
        "MTS_TRANSFER_LIB",
        "MTS_TRANSFER_CELL",
        "MTS_TRANSFER_ROOT",
        "MTS_TRANSFER_REPORT",
        "MTS_TARGET_OVERWRITE",
        "MTS_PROCESS_PID",
        "PWD",
        "OLDPWD",
        "SHLVL",
        "_",
        "PYTHONHOME",
        "PYTHONPATH",
        "LD_PRELOAD",
        "LD_AUDIT",
    }
    values = sorted(
        (str(key), str(value))
        for key, value in environment.items()
        if key not in ignored and not key.startswith("CDS_MPS_")
    )
    encoded = json.dumps(values, ensure_ascii=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _file_identity(
    path: str | Path,
    *,
    hash_contents: bool = False,
) -> dict[str, object]:
    """Return a non-sensitive path/stat identity for cache keys.

    Cadence executables can be large and live on a shared filesystem.  Their
    canonical path, inode, size, and nanosecond mtime are sufficient to detect
    replacement without reading the binary on every cache lookup.  Small
    caller-provided protocol scripts opt into a content digest below.
    """

    value = Path(path).expanduser().resolve()
    identity: dict[str, object] = {"path": str(value)}
    try:
        stat = value.stat()
    except OSError as exc:
        identity["error"] = type(exc).__name__
        return identity
    identity.update(
        {
            "size": int(stat.st_size),
            "mtime_ns": int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9))),
            "inode": int(getattr(stat, "st_ino", 0)),
        }
    )
    if hash_contents:
        try:
            digest = hashlib.sha256(value.read_bytes()).hexdigest()
        except OSError as exc:
            identity["read_error"] = type(exc).__name__
        else:
            identity["sha256"] = digest
    return identity


def _source_cache_key(
    source: Path,
    *,
    cds_fingerprint: str,
    dbaccess: str | None,
    dbaccess_script: str | Path | None,
    allow_fallback: bool,
    forbidden_target_cds_lib: str | Path | None,
    executable_environment: Mapping[str, str] | None = None,
) -> str:
    executable_path = None
    if dbaccess:
        if Path(dbaccess).is_absolute() or os.sep in dbaccess:
            executable_path = dbaccess
        elif executable_environment is not None:
            executable_path = shutil.which(
                dbaccess,
                path=executable_environment.get("PATH"),
            )
    executable_identity = (
        None if executable_path is None else _file_identity(executable_path)
    )
    payload = {
        "version": _SOURCE_CATALOG_CACHE_VERSION,
        "source": str(source),
        "cdslib_fingerprint": cds_fingerprint,
        "environment_digest": _cache_environment_digest(executable_environment or {}),
        "dbaccess": None if dbaccess is None else str(dbaccess),
        "dbaccess_identity": executable_identity,
        "dbaccess_script": (
            None
            if dbaccess_script is None
            else {
                "identity": _file_identity(dbaccess_script, hash_contents=True),
            }
        ),
        "bundled_script_version": _BUNDLED_DBACCESS_SCRIPT_VERSION,
        "allow_fallback": bool(allow_fallback),
        "forbidden_target": (
            None
            if forbidden_target_cds_lib is None
            else str(Path(forbidden_target_cds_lib).expanduser().resolve())
        ),
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()
