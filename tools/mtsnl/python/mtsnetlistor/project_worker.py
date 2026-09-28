"""Serialized, project-scoped persistent OCEAN runtime."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from threading import RLock
import time

from cadview.process_group import terminate_process_group

from .artifacts import atomic_write_text, sha256_file
from .environment import isolated_environment, write_cds_lib_overlay
from .errors import MtsNetlistorError
from .project_protocol import bootstrap, skill_string, task_script


_ACTIVE: ContextVar[ProjectWorker | None] = ContextVar(
    "mts_project_worker", default=None
)
_TASK_ENV = (
    "MTS_NETLISTOR_DEFAULTS_OUTPUT",
    "MTS_NETLISTOR_PROBE_PROJECT",
    "MTS_NETLISTOR_PROBE_RESULTS",
    "CADVIEW_CATALOG_OUTPUT",
    "MTS_NETLISTOR_WORKDIR",
)


def active_project_worker():
    return _ACTIVE.get()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class ProjectWorker:
    """A process page owns one source runtime, serialized through ``scope``.

    Ordinary tasks close their ADE session but retain loaded PDK code. Project
    or setup changes, failed operations and cancellation retire the process.
    Task logs are copied into existing run artifacts before temporary runtime
    files are removed. No environment values are persisted as audit metadata.
    """

    def __init__(self) -> None:
        self.process = None
        self.root = None
        self._identity = None
        self._setup = None
        self._source = None
        self._environment = None
        self._log = None
        self._offsets = {}
        self._sequence = 0
        self._command = ()
        self._started_at = ""
        self._runtime_pid = 0
        self._executable = None
        self._lock = RLock()
        self.starts = 0

    @contextmanager
    def scope(self, source, environment=None):
        from .catalog_fingerprint import _cache_environment_digest
        from .cdslib_fingerprint import _source_cdslib_fingerprint_details

        with self._lock:
            cds = Path(getattr(source, "cds_lib", source)).expanduser().resolve()
            env = dict(os.environ if environment is None else environment)
            fingerprint, _ = _source_cdslib_fingerprint_details(cds, env)
            identity = (str(cds), fingerprint, _cache_environment_digest(env))
            if identity != self._identity:
                self.close()
                self._identity, self._setup = identity, None
            # Startup files can modify tool globals. Changing their content or
            # removing one is a project configuration change, not a cell switch.
            if hasattr(source, "startup_file"):
                setup = tuple(
                    None
                    if path is None
                    else (str(Path(path).resolve()), sha256_file(path))
                    for path in (source.startup_file, source.simrc)
                )
                if self._setup is not None and setup != self._setup:
                    self.close()
                self._setup = setup
            self._source, self._environment = cds, env
            token = _ACTIVE.set(self)
            try:
                yield
            except BaseException:
                # Includes malformed defaults/MAE reports and post-netlisting
                # validation failures whose cleanup state cannot be trusted.
                self.close()
                raise
            finally:
                _ACTIVE.reset(token)

    def _start(self, executable, deadline, cancel, output_callback, chunks) -> None:
        from .catalog_fingerprint import _file_identity

        if cancel is not None and cancel.is_set():
            raise InterruptedError("project worker task canceled")
        if self._source is None:
            raise MtsNetlistorError("project worker requires a source scope")
        ocean = shutil.which(executable, path=self._environment.get("PATH"))
        if not ocean:
            raise MtsNetlistorError(
                f"project OCEAN executable is unavailable: {executable}"
            )
        executable_identity = _file_identity(ocean)
        if self.process is not None and self.process.poll() is None:
            if self._executable == executable_identity:
                return
        self.close()
        self.root = Path(tempfile.mkdtemp(prefix="mts-project-"))
        self.root.chmod(0o700)
        overlay = write_cds_lib_overlay(self._source, self.root / "source.cds.lib")
        env = isolated_environment(
            self._environment, cds_lib=overlay, workdir=self.root
        )
        boot = self.root / "worker.ocn"
        atomic_write_text(boot, bootstrap(self.root, ""))
        self._log = (self.root / "process.log").open("w+b")
        self._offsets = {}
        self._command = (
            ocean,
            "-nograph",
            "-nocdsinit",
            "-cdslib",
            str(overlay),
            "-replay",
            str(boot),
            "-log",
            str(self.root / "ocean.log"),
        )
        self._started_at = _timestamp()
        self._runtime_pid = 0
        self._executable = executable_identity
        self.process = subprocess.Popen(
            self._command,
            cwd=self.root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=self._log,
            stderr=self._log,
            start_new_session=True,
        )
        self.starts += 1
        self._wait(self.root / "ready", deadline, cancel, output_callback, chunks)
        self._runtime_pid = int((self.root / "ready").read_text().strip())

    def _output(self, output_callback=None) -> str:
        chunks = []
        if self.root is not None:
            for name in ("process.log", "tasks.log"):
                path = self.root / name
                if not path.is_file():
                    continue
                with path.open("rb") as stream:
                    stream.seek(self._offsets.get(name, 0))
                    data = stream.read()
                    self._offsets[name] = stream.tell()
                chunks.append(data.decode("utf-8", errors="replace"))
        text = "".join(chunks)
        if text and output_callback:
            try:
                output_callback(text)
            except BaseException:
                pass
        return text

    def _wait(self, status, deadline, cancel, output_callback, chunks) -> None:
        while True:
            chunks.append(self._output(output_callback))
            if cancel is not None and cancel.is_set():
                raise InterruptedError("project worker task canceled")
            if status.is_file():
                # SKILL drains the log before atomically publishing status.
                # Read again after observing status to avoid losing its tail.
                chunks.append(self._output(output_callback))
                return
            if self.process.poll() is not None:
                raise MtsNetlistorError(
                    f"project OCEAN exited with code {self.process.returncode}"
                )
            if (self.root / "fatal").exists():
                raise MtsNetlistorError("project worker command failed")
            if time.monotonic() >= deadline:
                raise TimeoutError("project worker task timed out")
            time.sleep(0.05)

    def warm(self, *, timeout=180.0, cancel=None, output_callback=None) -> None:
        """Start at project selection even when its catalog was cached."""
        with self._lock:
            chunks = []
            try:
                self._start(
                    "ocean", time.monotonic() + timeout, cancel, output_callback, chunks
                )
            except (TimeoutError, MtsNetlistorError) as exc:
                chunks.append(self.close(output_callback, diagnostics=True))
                raise MtsNetlistorError(f"{exc}\n{''.join(chunks)[-2000:]}") from exc
            except BaseException:
                self.close(output_callback)
                raise

    def run(
        self,
        script: Path,
        *,
        cwd: Path,
        environment,
        timeout=600.0,
        cancel=None,
        output_callback=None,
        log_file=None,
        executable="ocean",
    ):
        from .process import ProcessResult, environment_digest

        with self._lock:
            started = _timestamp()
            deadline = time.monotonic() + timeout
            timed_out = canceled = False
            chunks = []
            code, pid, task_id = 1, 0, ""
            status = task = None
            try:
                if cancel is not None and cancel.is_set():
                    raise InterruptedError("project worker task canceled")
                text = task_script(script.read_text(encoding="utf-8"))
                self._start(executable, deadline, cancel, output_callback, chunks)
                self._sequence += 1
                task_id = str(self._sequence)
                status = self.root / f"result-{task_id}"
                task = self.root / f"task-{task_id}.il"
                # Project environment/library selectors stay immutable. Only
                # private task output selectors are rebound for each command.
                prefix = [
                    f"setShellEnvVar({skill_string(name)} {skill_string(environment[name])})"
                    if name in environment
                    else f"unsetShellEnvVar({skill_string(name)})"
                    for name in _TASK_ENV
                ]
                if "CADVIEW_CATALOG_OUTPUT" in environment:
                    prefix.append("ddUpdateLibList()")
                atomic_write_text(task, "\n".join(prefix) + "\n" + text)
                atomic_write_text(
                    self.root / "command.il",
                    f"mtsProjectTask({skill_string(task)} {skill_string(status)} {skill_string(cwd)})\n",
                )
                self._wait(status, deadline, cancel, output_callback, chunks)
                code = 0 if status.read_text().strip() == "ok" else 1
            except (TimeoutError, InterruptedError, MtsNetlistorError) as exc:
                timed_out = isinstance(exc, TimeoutError)
                canceled = isinstance(exc, InterruptedError)
                chunks.append(str(exc) + "\n")
            except BaseException:
                self.close()
                raise
            pid = 0 if self.process is None else self.process.pid
            command, runtime_pid = self._command, self._runtime_pid
            worker_started = self._started_at
            if code:
                chunks.append(self.close(output_callback, diagnostics=True))
            elif task is not None:
                task.unlink(missing_ok=True)
                status.unlink(missing_ok=True)
            result = ProcessResult(
                argv=command,
                returncode=code,
                pid=pid,
                pgid=pid,
                stdout="".join(chunks),
                stderr="",
                timed_out=timed_out,
                canceled=canceled,
                ppid=os.getpid(),
                started_at=started,
                finished_at=_timestamp(),
                cwd=str(cwd),
                environment_digest=environment_digest(environment),
                lifetime="project",
                task_id=task_id,
                worker_started_at=worker_started,
                runtime_pid=runtime_pid,
            )
            if log_file:
                atomic_write_text(log_file, result.stdout)
            return result

    def run_catalog(
        self, command, environment, *, timeout, cancel_event, output_callback=None
    ):
        from cadview.catalog import CatalogCancelled, CatalogTimeout

        script = Path(command[command.index("-load") + 1])
        result = self.run(
            script,
            cwd=Path(environment["MTS_NETLISTOR_WORKDIR"]),
            environment=environment,
            timeout=timeout,
            cancel=cancel_event,
            output_callback=output_callback,
        )
        if result.canceled:
            raise CatalogCancelled("catalog request canceled")
        if result.timed_out:
            raise CatalogTimeout("catalog request timed out")
        return subprocess.CompletedProcess(
            command,
            result.returncode,
            result.stdout,
            result.stdout if result.returncode else "",
        )

    def close(self, output_callback=None, *, diagnostics=False) -> str:
        with self._lock:
            if self.process is not None:
                if self.process.poll() is None and self.root is not None:
                    (self.root / "shutdown").touch()
                    try:
                        self.process.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        pass
                # Retain ownership if teardown raises; a later close can retry.
                terminate_process_group(self.process)
                self.process = None
            output = self._output(output_callback)
            # The launcher transcript can contain a startup/crash diagnostic
            # that did not reach the live SKILL port. Read it only after exit;
            # -log buffering must never be used as a task completion signal.
            if diagnostics and self.root is not None:
                transcript = self.root / "ocean.log"
                if transcript.is_file():
                    with transcript.open("rb") as stream:
                        stream.seek(max(0, transcript.stat().st_size - 16384))
                        output += "\nOCEAN transcript tail:\n" + stream.read().decode(
                            "utf-8", errors="replace"
                        )
            if self._log is not None:
                self._log.close()
                self._log = None
            if self.root is not None:
                shutil.rmtree(self.root)
                self.root = None
            return output
