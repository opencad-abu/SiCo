"""Bounded dbAccess process execution and output capture."""

from __future__ import annotations

import codecs
import os
import subprocess
import tempfile
import time
from threading import Event
from typing import Callable, Mapping

from cadenv import cadence_mps_environment_names
from .catalog_errors import CatalogCancelled, CatalogError, CatalogTimeout
from .process_group import terminate_process_group

CatalogOutputCallback = Callable[[str], object]
_CATALOG_STREAM_POLL_BYTES = 256 * 1024

def _run_catalog_process(
    command: list[str],
    environ: Mapping[str, str],
    *,
    timeout: float,
    cancel_event: Event | None,
    output_callback: CatalogOutputCallback | None = None,
) -> subprocess.CompletedProcess[str]:
    if timeout <= 0:
        raise CatalogTimeout("catalog timeout must be greater than zero")
    mps_selectors = cadence_mps_environment_names(environ)
    if mps_selectors:
        raise CatalogError(
            "refusing dbAccess catalog with inherited MPS selector(s): "
            + ", ".join(mps_selectors)
        )
    # Keep the child streams off OS pipes. PDK libInit/dbAccess callbacks can
    # emit arbitrary diagnostics; if either pipe fills while the parent is
    # waiting for process termination, the child blocks and the timeout path
    # reports a false timeout. Temporary files avoid that back-pressure. Read
    # them with pread(2), which has an independent offset and therefore cannot
    # move the writer offset shared with the child process.
    process: subprocess.Popen[str] | None = None
    try:
        with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(
            mode="w+b"
        ) as stderr_file:
            try:
                process = subprocess.Popen(
                    command,
                    env=dict(environ),
                    stdout=stdout_file,
                    stderr=stderr_file,
                    start_new_session=True,
                )
            except (FileNotFoundError, PermissionError) as exc:
                raise CatalogError(
                    f"cannot execute dbAccess {command[0]!r}: {exc}"
                ) from exc

            stdout_offset = 0
            stderr_offset = 0
            stdout_bytes = bytearray()
            stderr_bytes = bytearray()
            stdout_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            stderr_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            output_finalized = False

            def emit_output(decoder: object, data: bytes, *, final: bool = False) -> None:
                if output_callback is None:
                    return
                text = decoder.decode(data, final=final)  # type: ignore[attr-defined]
                if not text:
                    return
                try:
                    output_callback(text)
                except BaseException:
                    # UI/log consumers can disappear while Cadence is
                    # starting. Diagnostics must never change catalog
                    # success, cancellation, or timeout semantics.
                    pass

            def drain_file(
                handle: object,
                offset: int,
                captured: bytearray,
                decoder: object,
                *,
                byte_limit: int | None,
            ) -> int:
                descriptor = handle.fileno()  # type: ignore[attr-defined]
                size = os.fstat(descriptor).st_size
                target = (
                    size
                    if byte_limit is None
                    else min(size, offset + max(0, byte_limit))
                )
                while True:
                    if target <= offset:
                        return offset
                    data = os.pread(
                        descriptor, min(64 * 1024, target - offset), offset
                    )
                    if not data:
                        return offset
                    offset += len(data)
                    captured.extend(data)
                    emit_output(decoder, data)

            def drain_output(*, final: bool = False) -> None:
                nonlocal stdout_offset, stderr_offset, output_finalized
                byte_limit = None if final else _CATALOG_STREAM_POLL_BYTES
                stdout_offset = drain_file(
                    stdout_file,
                    stdout_offset,
                    stdout_bytes,
                    stdout_decoder,
                    byte_limit=byte_limit,
                )
                stderr_offset = drain_file(
                    stderr_file,
                    stderr_offset,
                    stderr_bytes,
                    stderr_decoder,
                    byte_limit=byte_limit,
                )
                if final and not output_finalized:
                    output_finalized = True
                    emit_output(stdout_decoder, b"", final=True)
                    emit_output(stderr_decoder, b"", final=True)

            deadline = time.monotonic() + timeout
            while process.poll() is None:
                drain_output()
                if cancel_event is not None and cancel_event.is_set():
                    _stop_process(process)
                    drain_output(final=True)
                    raise CatalogCancelled("catalog request cancelled")
                if time.monotonic() >= deadline:
                    _stop_process(process)
                    drain_output(final=True)
                    raise CatalogTimeout(
                        f"catalog request timed out after {timeout:g}s"
                    )
                time.sleep(0.02)

            _stop_process(process)
            # The process group has exited, so one final read captures any bytes
            # written after the last polling iteration. Decode only after all
            # chunks are joined so a split UTF-8 sequence is handled once.
            drain_output(final=True)
            stdout = bytes(stdout_bytes).decode("utf-8", errors="replace")
            stderr = bytes(stderr_bytes).decode("utf-8", errors="replace")
            return subprocess.CompletedProcess(
                command, process.returncode, stdout, stderr
            )
    except OSError as exc:
        # Temporary-file creation or access failures are provider failures from
        # the caller's perspective.  Preserve the executable-specific message
        # above while making environmental failures actionable as well.
        if process is not None:
            try:
                _stop_process(process)
            except BaseException:
                # Preserve the original capture failure.  The process cleanup
                # is best effort because the original error may be a closed
                # descriptor or an already-disappeared process group.
                pass
        raise CatalogError(f"cannot capture dbAccess output: {exc}") from exc
    except BaseException:
        # A non-OSError from fstat/pread or an unexpected callback boundary
        # must not leave a detached dbAccess process running.  Cancellation,
        # timeout, and provider exceptions raised above are also covered here;
        # their original exception remains the caller-visible result.
        if process is not None:
            try:
                _stop_process(process)
            except BaseException:
                pass
        raise

def _stop_process(process: subprocess.Popen[str]) -> None:
    terminate_process_group(process, timeout=1.0)
