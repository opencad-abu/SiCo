"""Private durable storage for Calibre job receipts and result snapshots."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from pathlib import Path

from ..codec import artifact_ref
from ..errors import HubError
from ..limits import check_value
from .receipts import JobReceipt, validate_run_ref

_MAX_RECEIPT_BYTES = 256 * 1024
_MAX_RESULT_BYTES = 2 * 1024 * 1024
_MAX_PREPARED_BYTES = 32 * 1024 * 1024


class JobStore:
    """Keep one private receipt and bounded result metadata per Calibre job."""

    def __init__(self, work_root):
        root = Path(work_root).expanduser()
        if not root.is_absolute() or not root.is_dir():
            raise HubError("invalid_arguments", "Calibre work root is unavailable")
        self.work_root = root.resolve()
        self.root = self.work_root / ".sico_datahub" / "calibre"
        self._ensure_directory(self.work_root / ".sico_datahub")
        self._ensure_directory(self.root)
        self.jobs = self.root / "jobs"
        self.results = self.root / "results"
        self.logs = self.root / "logs"
        self.cache = self.root / "cache"
        self.prepared = self.root / "prepared"
        self.configs = self.root / "configs"
        self.candidates = self.root / "candidates"
        for path in (
            self.jobs, self.results, self.logs, self.cache,
            self.prepared, self.configs, self.candidates,
        ):
            self._ensure_directory(path)

    @staticmethod
    def _ensure_directory(path):
        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = path.lstat()
        except OSError as exc:
            raise HubError("read_failed", "Calibre state directory is unavailable") from exc
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.getuid()
        ):
            raise HubError("invalid_arguments", "Calibre state directory is not private")
        try:
            path.chmod(0o700)
        except OSError as exc:
            raise HubError("read_failed", "Calibre state directory is not writable") from exc

    @staticmethod
    def _run_ref(run_ref):
        return validate_run_ref(run_ref)

    def job_path(self, run_ref):
        return self.jobs / (self._run_ref(run_ref) + ".json")

    def result_path(self, run_ref):
        return self.results / (self._run_ref(run_ref) + ".json")

    def log_path(self, run_ref, suffix):
        if suffix not in {"out", "err"}:
            raise HubError("invalid_arguments", "Invalid Calibre log suffix")
        return self.logs / (self._run_ref(run_ref) + "." + suffix)

    def export_log_path(self, export_ref, suffix):
        if suffix not in {"out", "err"}:
            raise HubError("invalid_arguments", "Invalid Calibre export log suffix")
        return self.logs / (
            self._run_ref(export_ref) + ".export." + suffix
        )

    def prepared_deck_path(self, run_ref, kind):
        if kind not in {"drc", "lvs"}:
            raise HubError("invalid_arguments", "Invalid Calibre deck kind")
        return self.prepared / (self._run_ref(run_ref) + "." + kind + ".svrf")

    def config_path(self, run_ref):
        return self.configs / (self._run_ref(run_ref) + ".toml")

    def candidate_config_path(self, run_ref):
        return self.configs / (self._run_ref(run_ref) + ".candidate.toml")

    def candidate_run_dir(self, run_ref):
        path = self.candidates / self._run_ref(run_ref)
        self._ensure_directory(path)
        return path

    def exists(self, run_ref):
        return self.job_path(run_ref).exists()

    def create(self, receipt):
        if not isinstance(receipt, JobReceipt):
            raise HubError("invalid_arguments", "Expected a Calibre job receipt")
        self._write(self.job_path(receipt.run_ref), receipt.to_dict(), exclusive=True)

    def save(self, receipt):
        if not isinstance(receipt, JobReceipt):
            raise HubError("invalid_arguments", "Expected a Calibre job receipt")
        self._write(self.job_path(receipt.run_ref), receipt.to_dict())

    def load(self, run_ref):
        path = self.job_path(run_ref)
        try:
            info = path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_size > _MAX_RECEIPT_BYTES
            ):
                raise HubError("read_failed", "Calibre job receipt is invalid")
            raw = path.read_bytes()
        except FileNotFoundError as exc:
            raise HubError("stale_context", "Calibre job is unavailable") from exc
        except OSError as exc:
            raise HubError("read_failed", "Calibre job receipt cannot be read") from exc
        try:
            return JobReceipt.from_dict(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, ValueError, TypeError) as exc:
            raise HubError("read_failed", "Calibre job receipt is corrupt") from exc

    def write_result(self, run_ref, value):
        if not isinstance(value, dict):
            raise HubError("invalid_arguments", "Calibre result must be an object")
        check_value(value)
        self._write(self.result_path(run_ref), value)

    def write_metadata(self, run_ref, name, value):
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", name):
            raise HubError("invalid_arguments", "Invalid Calibre metadata name")
        if not isinstance(value, dict):
            raise HubError("invalid_arguments", "Calibre metadata must be an object")
        check_value(value)
        path = self.results / (self._run_ref(run_ref) + "." + name + ".json")
        self._write(path, value)
        return artifact_ref(path)

    def write_text(self, path, text):
        if not isinstance(text, str):
            raise HubError("invalid_arguments", "Calibre prepared text must be a string")
        self._write_bytes(path, text.encode("utf-8"), _MAX_PREPARED_BYTES)
        return artifact_ref(path)

    def read_result(self, run_ref):
        path = self.result_path(run_ref)
        try:
            info = path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_size > _MAX_RESULT_BYTES
            ):
                raise HubError("read_failed", "Calibre result snapshot is invalid")
            raw = path.read_bytes()
        except FileNotFoundError as exc:
            raise HubError(
                "source_not_captured", "Calibre result snapshot is unavailable"
            ) from exc
        except OSError as exc:
            raise HubError(
                "read_failed", "Calibre result snapshot cannot be read"
            ) from exc
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise HubError("read_failed", "Calibre result snapshot is corrupt") from exc
        if not isinstance(value, dict):
            raise HubError("read_failed", "Calibre result snapshot is invalid")
        check_value(value)
        return value

    def _write(self, path, value, *, exclusive=False):
        raw = (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        maximum = _MAX_RECEIPT_BYTES if path.parent == self.jobs else _MAX_RESULT_BYTES
        self._write_bytes(path, raw, maximum, exclusive=exclusive)

    @staticmethod
    def _write_bytes(path, raw, maximum, *, exclusive=False):
        if len(raw) > maximum:
            raise HubError("result_too_large", "Calibre state record exceeds its limit")
        if exclusive:
            JobStore._write_exclusive(path, raw)
            return
        descriptor, temporary = tempfile.mkstemp(
            prefix="." + path.name + ".", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = None
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            raise HubError("read_failed", "Calibre state record cannot be written") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _write_exclusive(path, raw):
        try:
            descriptor = os.open(
                str(path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                0o600,
            )
        except FileExistsError as exc:
            raise HubError("stale_context", "Calibre run_ref already exists") from exc
        except OSError as exc:
            raise HubError("read_failed", "Calibre job receipt cannot be created") from exc
        try:
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = None
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            path.unlink(missing_ok=True)
            raise HubError("read_failed", "Calibre job receipt cannot be written") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)


__all__ = ["JobStore"]
