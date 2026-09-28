"""Run cdsTextTo5x and validate its generated OA text view."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Mapping, Optional, Tuple

from .artifacts import master_file, validate_imported_view, view_snapshot
from .cdslib import resolve_library_path
from .errors import Nl2ViewError
from .nl2view_diagnostics import (
    append_wrapper_diagnostics,
    has_fatal_cadence_diagnostic,
    new_log_diagnostics,
)
from .nl2view_environment import import_environment
from .nl2view_models import ImportRequest
from .process import run_cadence_import


def _same_directory(first: Path, second: Path) -> bool:
    try:
        return os.path.samefile(first, second)
    except OSError:
        return first.resolve() == second.resolve()


def _copy_transition_source(
    request: ImportRequest, view_directory: Path, temporary: Path
) -> Tuple[ImportRequest, Optional[Path]]:
    """Force cdsTextTo5x to replace an existing link in copy mode."""

    if not request.copy_source or not view_directory.is_dir():
        return request, None
    try:
        master = master_file(view_directory)
        linked_to_source = master.is_symlink() and os.path.samefile(
            master, request.source
        )
    except (Nl2ViewError, OSError):
        linked_to_source = False
    if not linked_to_source:
        return request, None
    descriptor, staged_name = tempfile.mkstemp(
        prefix="nl2view-copy-", suffix=request.source.suffix, dir=temporary
    )
    os.close(descriptor)
    staged = Path(staged_name)
    try:
        shutil.copyfile(request.source, staged)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    return replace(request, source=staged), staged


def run_import(
    request: ImportRequest,
    environ: Optional[Mapping[str, str]] = None,
    *,
    timeout: Optional[float] = None,
    cancel: Optional[object] = None,
) -> int:
    """Run cdsTextTo5x and verify that it created or updated the target view."""

    environment, temporary = import_environment(request.copy_source, environ)
    library_path = resolve_library_path(
        request.cds_library_file,
        request.library,
        environment,
        cadence_executable=request.cds_lib_debug,
    )
    if request.expected_library_path and not _same_directory(
        library_path, request.expected_library_path
    ):
        raise Nl2ViewError(
            f"library {request.library!r} resolves to {library_path} in "
            f"{request.cds_library_file}, but the current Virtuoso session uses "
            f"{request.expected_library_path}"
        )
    view_directory = library_path / request.cell / request.view
    before = view_snapshot(view_directory)
    log_before = (
        request.log_file.read_bytes()
        if request.log_file and request.log_file.is_file()
        else None
    )
    invocation, staged_source = _copy_transition_source(
        request, view_directory, temporary
    )
    command = invocation.command()
    try:
        try:
            completed = run_cadence_import(
                command,
                environment,
                temporary,
                timeout=timeout,
                cancel=cancel,
            )
        except BaseException as exc:
            append_wrapper_diagnostics(
                request.log_file,
                command,
                returncode=None,
                outcome="execution_exception",
                detail=f"{type(exc).__name__}: {exc}",
            )
            raise
    finally:
        if staged_source is not None:
            staged_source.unlink(missing_ok=True)
    if completed.stdout:
        sys.stdout.write(completed.stdout)
    if completed.stderr:
        sys.stderr.write(completed.stderr)
    cadence_log = new_log_diagnostics(request.log_file, log_before)
    if completed.returncode != 0:
        append_wrapper_diagnostics(
            request.log_file,
            command,
            returncode=completed.returncode,
            outcome="cadence_exit_failure",
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        return completed.returncode
    diagnostics = "\n".join((completed.stdout, completed.stderr))
    diagnostics += "\n" + cadence_log
    if has_fatal_cadence_diagnostic(diagnostics):
        append_wrapper_diagnostics(
            request.log_file,
            command,
            returncode=completed.returncode,
            outcome="diagnostic_failure",
            stdout=completed.stdout,
            stderr=completed.stderr,
            detail="Cadence diagnostics contain an error/fatal line",
        )
        return 1
    try:
        validate_imported_view(request, library_path, before)
    except Nl2ViewError as exc:
        print(f"nl2view: {exc}", file=sys.stderr)
        append_wrapper_diagnostics(
            request.log_file,
            command,
            returncode=completed.returncode,
            outcome="artifact_validation_failure",
            stdout=completed.stdout,
            stderr=completed.stderr,
            detail=str(exc),
        )
        return 1
    append_wrapper_diagnostics(
        request.log_file,
        command,
        returncode=completed.returncode,
        outcome="succeeded",
        stdout=completed.stdout,
        stderr=completed.stderr,
        detail=f"validated OA text view: {view_directory}",
    )
    return 0
