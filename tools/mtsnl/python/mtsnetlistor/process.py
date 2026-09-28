"""Cadence worker routing and isolated process-group ownership."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import codecs
import hashlib
import json
import os
from queue import Empty, Queue
import select
import subprocess
from threading import Event, Thread
import time
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

from cadview.process_group import terminate_process_group

from .errors import IsolationError


# A callback is deliberately kept text-only.  Workers can report output from
# stdout/stderr and a simulator transcript through the same channel without
# coupling this process module to Qt or any other UI framework.
OutputCallback = Callable[[str], object]


@dataclass(frozen=True)
class ProcessResult:
    argv: tuple[str, ...]
    returncode: int
    pid: int
    pgid: int
    stdout: str
    stderr: str
    timed_out: bool = False
    canceled: bool = False
    # Audit metadata is optional for backwards compatibility with callers
    # constructing ProcessResult fixtures directly.
    ppid: int = 0
    started_at: str = ""
    finished_at: str = ""
    cwd: str = ""
    environment_digest: str = ""
    # For a resident runtime these timestamps delimit a task; the process
    # remains owned until project retirement. runtime_pid is Virtuoso itself
    # when the ocean launcher is a separate process-group leader.
    lifetime: str = "task"
    task_id: str = ""
    worker_started_at: str = ""
    runtime_pid: int = 0


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def environment_digest(environment: Mapping[str, str]) -> str:
    """Return a deterministic digest without persisting environment values."""

    encoded = json.dumps(
        sorted((str(key), str(value)) for key, value in environment.items()),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def run_isolated(
    argv: Sequence[str],
    *,
    cwd: str | Path,
    environment: Mapping[str, str],
    timeout: Optional[float] = None,
    cancel: Optional[object] = None,
    log_file: Optional[str | Path] = None,
    output_callback: Optional[OutputCallback] = None,
    monitor_file: Optional[str | Path] = None,
) -> ProcessResult:
    """Run a Cadence task, routing source OCEAN to an active project runtime.

    Without a project scope, create a new process group and terminate all
    descendants on exit. Other commands always retain this one-shot behavior.

    ``output_callback`` receives output incrementally while the worker is
    running.  In addition to the child's stdout/stderr, ``monitor_file`` can
    be supplied for programs such as OCEAN which write their useful transcript
    to a separate ``-log`` file.  Callback exceptions are intentionally
    ignored: diagnostics must never change the worker's exit status.
    """

    mps_selectors = tuple(
        sorted(name for name in environment if name.startswith("CDS_MPS_"))
    )
    if mps_selectors:
        raise IsolationError(
            "refusing isolated worker with inherited Cadence MPS selector(s): "
            + ", ".join(mps_selectors)
        )
    from .project_worker import active_project_worker
    project = active_project_worker()
    if project is not None and "-replay" in argv:
        script = Path(argv[list(argv).index("-replay") + 1])
        return project.run(script, cwd=Path(cwd), environment=environment,
                           timeout=timeout or 600.0, cancel=cancel,
                           output_callback=output_callback, log_file=log_file,
                           executable=str(argv[0]))
    command = tuple(str(item) for item in argv)
    resolved_cwd = str(Path(cwd).resolve())
    parent_pid = os.getpid()
    started_at = _timestamp()
    process = subprocess.Popen(
        list(command),
        cwd=resolved_cwd,
        env=dict(environment),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        start_new_session=True,
    )

    readers: tuple[Thread, ...] = ()
    stop_readers = Event()
    group_finished = False
    try:
        # ``communicate()`` cannot be used together with live output forwarding:
        # it would wait until process exit before exposing either pipe.  Dedicated
        # readers keep both pipes drained (avoiding child-side pipe backpressure)
        # while the supervisor loop polls cancellation, timeout, and the optional
        # simulator transcript.
        output_queue: Queue[str] = Queue()
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        def _read_stream(stream: object, chunks: list[str]) -> None:
            # Popen's text streams expose ``readline``; keeping this helper typed
            # loosely also makes it straightforward to use with test doubles.
            if stream is None:
                return
            descriptor = stream.fileno()
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            try:
                while True:
                    ready, _, _ = select.select([descriptor], [], [], 0.05)
                    if not ready:
                        if stop_readers.is_set():
                            break
                        continue
                    data = os.read(descriptor, 64 * 1024)
                    if not data:
                        break
                    chunk = decoder.decode(data)
                    if chunk:
                        chunks.append(chunk)
                        output_queue.put(chunk)
                    if stop_readers.is_set():
                        break
                tail = decoder.decode(b"", final=True)
                if tail:
                    chunks.append(tail)
                    output_queue.put(tail)
            except (OSError, ValueError):
                # A stream can be closed as part of process-group teardown.  Any
                # data already read remains available in the accumulated chunks.
                return

        readers = (
            Thread(
                target=_read_stream,
                args=(process.stdout, stdout_chunks),
                name="mts-netlistor-stdout-reader",
                daemon=True,
            ),
            Thread(
                target=_read_stream,
                args=(process.stderr, stderr_chunks),
                name="mts-netlistor-stderr-reader",
                daemon=True,
            ),
        )
        for reader in readers:
            reader.start()

        monitor_path = (
            Path(monitor_file).expanduser().resolve() if monitor_file is not None else None
        )
        monitor_offset = 0
        monitor_inode: int | None = None

        def _poll_monitor_file() -> None:
            """Queue bytes appended to ``monitor_file`` since the last poll."""

            nonlocal monitor_offset, monitor_inode
            if monitor_path is None:
                return
            try:
                stat_result = monitor_path.stat()
                inode = getattr(stat_result, "st_ino", None)
                # OCEAN may rotate or truncate its transcript.  Reset the cursor
                # when that happens so the new file is not silently skipped.
                if (
                    monitor_inode is not None
                    and inode != monitor_inode
                ) or stat_result.st_size < monitor_offset:
                    monitor_offset = 0
                monitor_inode = inode
                if stat_result.st_size <= monitor_offset:
                    return
                with monitor_path.open("rb") as handle:
                    handle.seek(monitor_offset)
                    data = handle.read()
                if not data:
                    return
                monitor_offset += len(data)
                output_queue.put(data.decode("utf-8", errors="replace"))
            except (OSError, ValueError):
                # The transcript is optional and may not exist until OCEAN starts.
                return

        def _drain_output() -> None:
            if output_callback is None:
                # Still clear the queue so a very chatty worker cannot retain
                # unnecessary duplicate strings while final output is accumulated.
                while True:
                    try:
                        output_queue.get_nowait()
                    except Empty:
                        return
            while True:
                try:
                    text = output_queue.get_nowait()
                except Empty:
                    return
                if not text:
                    continue
                try:
                    output_callback(text)
                except BaseException:
                    # UI callbacks can be torn down while a worker is finishing;
                    # never let that interfere with process supervision.
                    continue

        started = time.monotonic()
        timed_out = False
        canceled = False
        while True:
            _poll_monitor_file()
            _drain_output()
            process_running = process.poll() is None
            if not process_running:
                # One final pass catches a transcript write racing with process
                # exit or the last reader-thread delivery.  Do not wait for
                # reader threads indefinitely: a descendant that inherited a
                # pipe may keep it open after the supervised process exits.
                _poll_monitor_file()
                _drain_output()
                break
            if process_running and timeout is not None and time.monotonic() - started >= timeout:
                timed_out = True
                break
            elif process_running and cancel is not None and getattr(cancel, "is_set", lambda: False)():
                canceled = True
                break
            # Keep the polling interval short enough for a responsive GUI without
            # spinning when the worker has no output.
            time.sleep(0.05)
        # A successful/failed parent exit does not imply its Cadence helpers
        # exited. Release their OA handles and inherited pipes before readback.
        terminate_process_group(process)
        group_finished = True
        for reader in readers:
            reader.join(timeout=1.0)
        _poll_monitor_file()
        _drain_output()
        stdout = "".join(stdout_chunks)
        stderr = "".join(stderr_chunks)
        if log_file is not None:
            target = Path(log_file).expanduser().resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(stdout + stderr, encoding="utf-8", errors="replace")
        try:
            process_group = os.getpgid(process.pid)
        except ProcessLookupError:
            # start_new_session makes the child PID its PGID.  The process can
            # disappear before this lookup, so retain the deterministic value.
            process_group = process.pid
        return ProcessResult(
            command,
            int(process.returncode or 0),
            process.pid,
            process_group,
            stdout,
            stderr,
            timed_out,
            canceled,
            parent_pid,
            started_at,
            _timestamp(),
            resolved_cwd,
            environment_digest(environment),
        )
    finally:
        # Includes failures in supervision, reader setup or durable logging.
        try:
            if not group_finished:
                terminate_process_group(process)
        finally:
            stop_readers.set()
            for reader in readers:
                if reader.ident is not None:
                    reader.join(timeout=1.0)
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
