"""Durable project-service cancel intents for visible-DRC jobs."""

from __future__ import annotations

import json
import os
import re
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path

_JOB = re.compile(r"^visible-[a-f0-9]{32}$")


class VisibleDrcCancel:
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
            raise OSError("Visible DRC cancel directory is unavailable")
        os.chmod(self.directory, 0o700)

    def request(self, job_ref, requester=""):
        path = self._path(job_ref)
        self._ensure_directory()
        payload = (
            json.dumps({
                "schema": "cad.visible_drc.cancel.v1",
                "job_ref": job_ref,
                "requested_at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"
                ),
                "requester": str(requester or "")[:128],
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

    def requested(self, job_ref):
        path = self._path(job_ref)
        try:
            descriptor = os.open(
                str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            )
        except OSError:
            return False
        try:
            info = os.fstat(descriptor)
            return stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
        finally:
            os.close(descriptor)

    def clear(self, job_ref):
        try:
            self._path(job_ref).unlink()
        except FileNotFoundError:
            pass


__all__ = ["VisibleDrcCancel"]
