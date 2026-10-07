"""Validate and transfer one bounded Virtuoso artifact to the private spool."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .socket_server import RequestFailure
from .skill_diagnostics import carry_output


def materialize_snapshot_artifact(
    payload: dict[str, Any], expected_path: Path, source_spool: Path,
    destination_spool: Path, *, artifact_prefix: str, max_bytes: int,
) -> dict[str, Any]:
    try:
        result = _materialize_snapshot_artifact(payload, expected_path, source_spool,
            destination_spool, artifact_prefix=artifact_prefix, max_bytes=max_bytes)
        return carry_output(result, payload)
    except RequestFailure as exc:
        exc.data = carry_output(exc.data, payload)
        raise


def _materialize_snapshot_artifact(
    payload, expected_path, source_spool, destination_spool, *, artifact_prefix, max_bytes,
):
    metadata: Mapping[str, Any] = payload
    value = payload.get("value")
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            decoded = None
        if isinstance(decoded, Mapping):
            metadata = decoded
    if metadata.get("ok") is False:
        raise RequestFailure(
            "skill_error", str(metadata.get("error", "schematic snapshot failed")), payload
        )
    artifact_path = metadata.get("artifact_path")
    if not isinstance(artifact_path, str):
        raise RequestFailure("invalid_result", "Virtuoso snapshot did not return an artifact")
    source = Path(artifact_path)
    if source != expected_path:
        expected_path.unlink(missing_ok=True)
        try:
            source_parent = source.expanduser().resolve(strict=False).parent
            expected_parent = source_spool.resolve(strict=True)
        except OSError:
            source_parent = None
            expected_parent = None
        if source_parent != expected_parent:
            raise RequestFailure(
                "invalid_result", "snapshot artifact escaped the session spool"
            )
        raise RequestFailure(
            "invalid_result", "Virtuoso returned an unexpected snapshot artifact"
        )
    try:
        expected_parent = source_spool.resolve(strict=True)
        source_parent = source.parent.resolve(strict=True)
    except OSError as exc:
        raise RequestFailure("invalid_result", "snapshot artifact is unavailable") from exc
    if source_parent != expected_parent:
        raise RequestFailure("invalid_result", "snapshot artifact escaped the session spool")
    if (
        not source.name.startswith(artifact_prefix)
        or source.suffix != ".jsonl"
    ):
        raise RequestFailure("invalid_result", "snapshot artifact name is invalid")
    try:
        path_info = source.lstat()
    except OSError as exc:
        raise RequestFailure("invalid_result", "snapshot artifact is unavailable") from exc
    if (
        stat.S_ISLNK(path_info.st_mode)
        or not stat.S_ISREG(path_info.st_mode)
        or path_info.st_uid != os.getuid()
        or path_info.st_nlink != 1
        or path_info.st_size <= 0
        or path_info.st_size > max_bytes
    ):
        try:
            source.unlink(missing_ok=True)
        except OSError:
            pass
        raise RequestFailure(
            "invalid_result", "snapshot artifact is not a bounded regular file"
        )
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    fd = -1
    destination: Path | None = None
    try:
        try:
            fd = os.open(source, flags)
        except OSError as exc:
            raise RequestFailure(
                "invalid_result", "snapshot artifact cannot be read safely"
            ) from exc
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_dev != path_info.st_dev
            or info.st_ino != path_info.st_ino
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_size != path_info.st_size
            or info.st_size <= 0
            or info.st_size > max_bytes
        ):
            raise RequestFailure(
                "invalid_result", "snapshot artifact is not a bounded regular file"
            )
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            destination = destination_spool / f"{artifact_prefix}{secrets.token_hex(12)}.jsonl"
            out_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                out_flags |= os.O_NOFOLLOW
            out_fd = os.open(destination, out_flags, 0o600)
            digest = hashlib.sha256()
            size = 0
            try:
                with os.fdopen(out_fd, "wb") as output:
                    out_fd = -1
                    while chunk := handle.read(1024 * 1024):
                        size += len(chunk)
                        if size > max_bytes:
                            raise RequestFailure(
                                "result_too_large", "snapshot artifact exceeds the size limit"
                            )
                        output.write(chunk)
                        digest.update(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            except BaseException:
                if out_fd >= 0:
                    os.close(out_fd)
                destination.unlink(missing_ok=True)
                raise
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            source.unlink(missing_ok=True)
        except OSError:
            pass
    assert destination is not None
    if size != info.st_size:
        destination.unlink(missing_ok=True)
        raise RequestFailure("invalid_result", "snapshot artifact changed while being copied")
    return {
        "ok": True,
        "artifact": {
            "path": str(destination),
            "size": size,
            "sha256": digest.hexdigest(),
        },
    }
