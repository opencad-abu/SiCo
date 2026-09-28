"""Run directory, digest, locking, and atomic stable-output helpers."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Iterator, Mapping, Optional

from .errors import RequestValidationError


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def stable_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def request_digest(value: Mapping[str, object]) -> str:
    return sha256_bytes(stable_json(value).encode("utf-8"))


def atomic_write_text(path: str | Path, text: str, *, refuse_existing: bool = False) -> Path:
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if refuse_existing:
            os.link(temporary, destination)
            temporary.unlink(missing_ok=True)
        else:
            os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


@dataclass(frozen=True)
class JobPaths:
    """Stable namespace plus one isolated run directory."""

    namespace: Path
    run: Path
    source: Path
    raw: Path
    scoped: Path
    transfer: Path
    publish: Path
    logs: Path

    @classmethod
    def namespace_for(
        cls,
        source_lib: str,
        source_cell: str,
        source_view: str,
        *,
        environ: Optional[Mapping[str, str]] = None,
    ) -> Path:
        """Resolve and prepare the design namespace without allocating a run.

        Namespace preparation is intentionally separate from run allocation so
        callers can acquire the per-design lock *before* creating a run
        directory.  This prevents two same-second requests from racing while
        constructing their artifact trees.
        """

        environment = os.environ if environ is None else environ
        configured = str(environment.get("PROJ_ADE_DB_DIR", "")).strip()
        if not configured:
            raise RequestValidationError("PROJ_ADE_DB_DIR is not set")
        root = Path(configured).expanduser()
        if not root.is_absolute():
            raise RequestValidationError("PROJ_ADE_DB_DIR must be an absolute path")
        root = root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        if not root.is_dir() or not os.access(root, os.W_OK):
            raise RequestValidationError(f"PROJ_ADE_DB_DIR is not writable: {root}")

        # The request model validates these fields, but JobPaths is also used
        # directly by inspection/tests.  Never allow a caller to escape the
        # PROJ_ADE_DB_DIR namespace through a path-like design name.
        for label, value in (("source library", source_lib), ("source cell", source_cell), ("source view", source_view)):
            text = str(value)
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", text):
                raise RequestValidationError(f"invalid {label}: {value!r}")
        namespace = root / f"{source_lib}.{source_cell}.{source_view}"
        namespace.mkdir(parents=True, exist_ok=True)
        return namespace

    @classmethod
    def create(
        cls,
        source_lib: str,
        source_cell: str,
        source_view: str,
        request_digest_value: str,
        *,
        environ: Optional[Mapping[str, str]] = None,
        namespace: Optional[str | Path] = None,
    ) -> "JobPaths":
        resolved_namespace = (
            Path(namespace).expanduser().resolve()
            if namespace is not None
            else cls.namespace_for(
                source_lib,
                source_cell,
                source_view,
                environ=environ,
            )
        )
        if not resolved_namespace.is_dir():
            raise RequestValidationError(f"job namespace is unavailable: {resolved_namespace}")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", str(request_digest_value)):
            raise RequestValidationError("request digest must be a 64-character hexadecimal string")
        runs = resolved_namespace / ".mts-netlistor" / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        stem = f"{timestamp}-{str(request_digest_value)[:16]}"
        # Microseconds make normal collisions vanishingly unlikely; the loop
        # is still required because callers/tests may deliberately freeze the
        # clock or retry an identical request concurrently.
        run = runs / stem
        for attempt in range(100):
            candidate = run if attempt == 0 else runs / f"{stem}-{attempt:02d}"
            try:
                candidate.mkdir(parents=False, exist_ok=False)
                run = candidate
                break
            except FileExistsError:
                continue
        else:  # pragma: no cover - practically unreachable on a healthy FS
            raise RequestValidationError(f"unable to allocate unique run directory under {runs}")
        source = run / "source"
        raw = source / "raw"
        scoped = run / "scoped"
        transfer = run / "transfer"
        publish = run / "publish"
        logs = run / "logs"
        for directory in (raw, scoped, transfer, publish, logs):
            directory.mkdir(parents=True, exist_ok=False)
        # A small immutable ownership marker lets publication distinguish a
        # run allocated by this workflow from an arbitrary caller directory.
        atomic_write_text(
            run / ".mts-netlistor-run.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "request_digest": str(request_digest_value),
                    "source_library": source_lib,
                    "source_cell": source_cell,
                    "source_view": source_view,
                },
                sort_keys=True,
            )
            + "\n",
            refuse_existing=True,
        )
        return cls(resolved_namespace, run, source, raw, scoped, transfer, publish, logs)

    @property
    def latest(self) -> Path:
        return self.namespace / "latest.json"

    @property
    def lock_file(self) -> Path:
        return self.namespace / ".mts-netlistor.lock"

    def stable_output(self, cell: str, suffix: str) -> Path:
        return self.namespace / f"{cell}{suffix}"


@contextmanager
def exclusive_job_lock(path: str | Path) -> Iterator[object]:
    """Hold one advisory lock per source design namespace."""

    import fcntl

    lock_path = Path(path).expanduser().resolve()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    stream = lock_path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RequestValidationError(f"another MTS run owns {lock_path}") from exc
        stream.seek(0)
        stream.truncate()
        stream.write(json.dumps({
            "pid": os.getpid(),
            "ppid": os.getppid(),
            "start_time": _proc_start_time(os.getpid()),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }, sort_keys=True))
        stream.flush()
        yield stream
    finally:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


def _proc_start_time(pid: int) -> str:
    """Best-effort process identity marker used for stale-lock diagnostics."""

    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        fields = text.rsplit(")", 1)[-1].split()
        return fields[19] if len(fields) > 19 else ""
    except (OSError, ValueError, IndexError):
        return ""
