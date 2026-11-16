"""Durable workspace index for restart-safe visible-DRC job lookup."""

from __future__ import annotations

import json
import math
import os
import re
import stat
import uuid
from pathlib import Path

from .visible_drc_cancel import VisibleDrcCancel
from .visible_drc_events import VisibleDrcEvents
from .visible_drc_lease import VisibleDrcLeases

SCHEMA = "cad.visible_drc.job.v1"
_JOB = re.compile(r"^visible-[a-f0-9]{32}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_MAX_BYTES = 16 * 1024
_MAX_WARNINGS = 16
_MAX_COUNTS = 32
_MAX_PATH = 4096
_MAX_TEXT = 512
_STATES = frozenset({
    "queued",
    "running",
    "clean",
    "violations",
    "failed",
    "unknown",
    "correct",
    "incorrect",
    "cancelled",
})
_TERMINAL_STATES = _STATES - {"queued", "running"}


def _text(value, field, *, maximum=_MAX_TEXT):
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError(f"{field} is invalid")
    return value


def _absolute(value, field):
    text = _text(value, field, maximum=_MAX_PATH)
    if not Path(text).is_absolute():
        raise ValueError(f"{field} must be absolute")
    return text


def _bbox(value):
    if (
        not isinstance(value, list)
        or len(value) != 4
        or any(type(item) not in {int, float} for item in value)
        or not all(math.isfinite(float(item)) for item in value)
        or value[2] < value[0]
        or value[3] < value[1]
    ):
        raise ValueError("bbox is invalid")
    return list(value)


def _warnings(value):
    if not isinstance(value, list) or len(value) > _MAX_WARNINGS:
        raise ValueError("warnings are invalid")
    return [_text(item, "warning", maximum=128) for item in value]


def _counts(value):
    if not isinstance(value, dict) or len(value) > _MAX_COUNTS:
        raise ValueError("counts are invalid")
    result = {}
    for key, item in value.items():
        result[_text(key, "count name", maximum=128)] = _count(item, "count")
    return result


def _count(value, field):
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} is invalid")
    return value


def _optional_sha(value, field):
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def _recheck_of(value):
    if value in (None, ""):
        return ""
    text = _text(value, "recheck_of", maximum=64)
    if _JOB.fullmatch(text) is None:
        raise ValueError("recheck_of is invalid")
    return text


def _region_source(value):
    if value in (None, ""):
        return "visible"
    if value not in {"visible", "explicit"}:
        raise ValueError("region_source is invalid")
    return value


def _state(value):
    if value in (None, ""):
        return "queued"
    if value not in _STATES:
        raise ValueError("state is invalid")
    return value


def _record(value):
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise ValueError("record schema is invalid")
    job_ref = _text(value.get("job_ref"), "job_ref", maximum=64)
    if _JOB.fullmatch(job_ref) is None:
        raise ValueError("job_ref is invalid")
    timeout = value.get("timeout_seconds")
    if type(timeout) not in {int, float} or not 0 < float(timeout) <= 1800:
        raise ValueError("timeout_seconds is invalid")
    size = value.get("size")
    if size is not None:
        _count(size, "size")
    sha256 = value.get("sha256")
    if sha256 is not None and (
        not isinstance(sha256, str) or _SHA256.fullmatch(sha256) is None
    ):
        raise ValueError("sha256 is invalid")
    return {
        "schema": SCHEMA,
        "job_ref": job_ref,
        "window_ref": _text(value.get("window_ref"), "window_ref", maximum=128),
        "config_path": _absolute(value.get("config_path"), "config_path"),
        "work_root": _absolute(value.get("work_root"), "work_root"),
        "calibre_root": _absolute(value.get("calibre_root"), "calibre_root"),
        "timeout_seconds": float(timeout),
        "candidate": _absolute(value.get("candidate"), "candidate"),
        "bbox": _bbox(value.get("bbox")),
        "top_cell": _text(value.get("top_cell"), "top_cell"),
        "size": size,
        "sha256": sha256,
        "warnings": _warnings(value.get("warnings", [])),
        "counts": _counts(value.get("counts", {})),
        "config_sha256": _optional_sha(value.get("config_sha256"), "config_sha256"),
        "runset_dependencies_sha256": _optional_sha(
            value.get("runset_dependencies_sha256"), "runset_dependencies_sha256"
        ),
        "recheck_of": _recheck_of(value.get("recheck_of")),
        "region_source": _region_source(value.get("region_source")),
        "state": _state(value.get("state")),
    }


def record_from_values(values):
    export = values["export"]
    return {
        "schema": SCHEMA,
        "job_ref": values["run_ref"],
        "window_ref": values["window_ref"],
        "config_path": str(values["config_path"]),
        "work_root": str(values["work_root"]),
        "calibre_root": str(values["calibre_root"]),
        "timeout_seconds": values["timeout"],
        "candidate": str(values["candidate"]),
        "bbox": list(values["bbox"]),
        "top_cell": values["flow"].top_cell,
        "size": export.get("size"),
        "sha256": export.get("sha256"),
        "warnings": export.get("warnings", []),
        "counts": export.get("counts", {}),
        "config_sha256": values.get("config_sha256", ""),
        "runset_dependencies_sha256": values.get(
            "runset_dependencies_sha256", ""
        ),
        "recheck_of": values.get("recheck_of") or "",
        "region_source": export.get("region_source", "visible"),
        "state": values.get("state", "queued"),
    }


class VisibleDrcRegistry:
    """Store bounded job locators under one workspace-owned private directory."""

    def __init__(self, workspace):
        root = Path(workspace).expanduser()
        if not root.is_absolute():
            raise ValueError("workspace must be absolute")
        self.directory = root / ".sico_datahub" / "visible" / "jobs"
        self.events = VisibleDrcEvents(self.directory.parent / "events")
        self.cancel = VisibleDrcCancel(self.directory.parent / "cancel")
        self.leases = VisibleDrcLeases(self.directory.parent / "leases")

    def _path(self, job_ref):
        if not isinstance(job_ref, str) or _JOB.fullmatch(job_ref) is None:
            raise ValueError("job_ref is invalid")
        return self.directory / (job_ref + ".json")

    def _ensure_directory(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.getuid()
        ):
            raise OSError("Visible DRC registry is unavailable")
        os.chmod(self.directory, 0o700)

    def save(self, record):
        value = _record(record)
        payload = (
            json.dumps(value, ensure_ascii=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        if len(payload) > _MAX_BYTES:
            raise ValueError("record is too large")
        self._ensure_directory()
        path = self._path(value["job_ref"])
        temporary = path.with_name(
            "." + path.name + "." + str(os.getpid()) + "." + uuid.uuid4().hex
        )
        descriptor = os.open(
            str(temporary),
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        try:
            os.replace(temporary, path)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        return path

    def load(self, job_ref):
        path = self._path(job_ref)
        try:
            descriptor = os.open(
                str(path),
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise OSError("Visible DRC registry record is unavailable") from exc
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_size > _MAX_BYTES
            ):
                raise OSError("Visible DRC registry record is invalid")
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = None
                payload = stream.read(_MAX_BYTES + 1)
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if len(payload) > _MAX_BYTES:
            raise OSError("Visible DRC registry record is too large")
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("Visible DRC registry record is corrupt") from exc
        record = _record(value)
        if record["job_ref"] != job_ref:
            raise ValueError("Visible DRC registry job_ref does not match")
        return record

    def update_state(self, job_ref, state):
        record = self.load(job_ref)
        if record is None:
            raise ValueError("Visible DRC registry record is unavailable")
        record["state"] = state
        return self.save(record)

    def latest(self, values):
        current_config = values.get("config_sha256") or ""
        if not current_config:
            return None
        try:
            paths = sorted(
                self.directory.glob("*.json"),
                key=lambda path: path.stat().st_mtime_ns,
                reverse=True,
            )[:64]
        except OSError:
            return None
        for path in paths:
            job_ref = path.stem
            if _JOB.fullmatch(job_ref) is None:
                continue
            try:
                record = self.load(job_ref)
            except (OSError, ValueError):
                continue
            if record is None or record["state"] not in _TERMINAL_STATES:
                continue
            if (
                record["config_sha256"] != current_config
                or record["runset_dependencies_sha256"]
                != (values.get("runset_dependencies_sha256") or "")
                or record["config_path"] != str(values["config_path"])
                or record["work_root"] != str(values["work_root"])
                or record["calibre_root"] != str(values["calibre_root"])
                or record["top_cell"] != values["flow"].top_cell
                or list(record["bbox"]) != list(values["bbox"])
            ):
                continue
            return record
        return None

    def list_jobs(self, offset, limit):
        try:
            paths = sorted(
                self.directory.glob("*.json"),
                key=lambda path: path.stat().st_mtime_ns,
                reverse=True,
            )[:256]
        except OSError:
            return []
        rows = []
        for path in paths:
            job_ref = path.stem
            if _JOB.fullmatch(job_ref) is None:
                continue
            try:
                record = self.load(job_ref)
                updated = path.stat().st_mtime_ns
            except (OSError, ValueError):
                continue
            if record is None:
                continue
            rows.append({
                "job_ref": record["job_ref"],
                "window_ref": record["window_ref"],
                "top_cell": record["top_cell"],
                "bbox": record["bbox"],
                "region_source": record["region_source"],
                "state": record["state"],
                "config_path": record["config_path"],
                "work_root": record["work_root"],
                "calibre_root": record["calibre_root"],
                "candidate": record["candidate"],
                "size": record["size"],
                "sha256": record["sha256"],
                "dependencies_sha256": record["runset_dependencies_sha256"],
                "config_sha256": record["config_sha256"],
                "recheck_of": record["recheck_of"],
                "updated_mtime_ns": updated,
            })
        return rows[offset:offset + limit]

    def remove(self, job_ref):
        try:
            self._path(job_ref).unlink()
        except FileNotFoundError:
            pass
        self.events.remove(job_ref)
        self.clear_cancel(job_ref)
        self.remove_lease(job_ref)

    def request_cancel(self, job_ref, requester=""):
        return self.cancel.request(job_ref, requester)

    def cancel_requested(self, job_ref):
        return self.cancel.requested(job_ref)

    def clear_cancel(self, job_ref):
        return self.cancel.clear(job_ref)

    def write_lease(self, job_ref, owner_pid, updated_at=None):
        return self.leases.write(job_ref, owner_pid, updated_at)

    def read_lease(self, job_ref):
        return self.leases.read(job_ref)

    def remove_lease(self, job_ref):
        return self.leases.remove(job_ref)

    def append_event(self, job_ref, state, message=""):
        return self.events.append(job_ref, state, message)

    def read_events(self, job_ref, after=0, limit=32):
        return self.events.read(job_ref, after, limit)


__all__ = ["SCHEMA", "VisibleDrcRegistry", "record_from_values"]
