"""Heartbeat leases for MCP-owned visible-DRC jobs."""

from __future__ import annotations

import json
import os
import re
import stat
import time
import uuid
from pathlib import Path

_JOB = re.compile(r"^visible-[a-f0-9]{32}$")


class VisibleDrcLeases:
    def __init__(self, directory):
        self.directory = Path(directory)

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
            raise OSError("Visible DRC lease directory is unavailable")
        os.chmod(self.directory, 0o700)

    def write(self, job_ref, owner_pid, updated_at=None):
        path = self._path(job_ref)
        self._ensure_directory()
        if type(owner_pid) is not int or owner_pid <= 0:
            raise ValueError("owner_pid is invalid")
        timestamp = time.time() if updated_at is None else float(updated_at)
        payload = (
            json.dumps({
                "schema": "cad.visible_drc.lease.v1",
                "job_ref": job_ref,
                "owner_pid": owner_pid,
                "updated_at": timestamp,
            }, ensure_ascii=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
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
            os.replace(temporary, path)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        return path

    def read(self, job_ref):
        path = self._path(job_ref)
        try:
            descriptor = os.open(
                str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            )
        except OSError:
            return None
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_size > 4096
            ):
                return None
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = None
                payload = stream.read(4097)
        finally:
            if descriptor is not None:
                os.close(descriptor)
        try:
            value = json.loads(payload.decode("utf-8"))
            if (
                not isinstance(value, dict)
                or value.get("schema") != "cad.visible_drc.lease.v1"
                or value.get("job_ref") != job_ref
                or type(value.get("owner_pid")) is not int
                or type(value.get("updated_at")) not in {int, float}
            ):
                return None
            return value
        except (UnicodeDecodeError, ValueError):
            return None

    def remove(self, job_ref):
        try:
            self._path(job_ref).unlink()
        except FileNotFoundError:
            pass


__all__ = ["VisibleDrcLeases"]
