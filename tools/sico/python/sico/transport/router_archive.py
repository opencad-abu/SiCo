"""Sealed-generation maintenance with compatibility record exports; receipts and ownership evidence persist."""

from __future__ import annotations

import fcntl
from sicolock import lock as state_lock
import gzip
import hashlib
import json
import os
import re
import socket
import stat
import time
from contextlib import ExitStack
from pathlib import Path

from ..core.contracts import identifier
from ..storage.journal import open_private, private_dir, sync_directory
from ..storage.roots import agent_root
from .router_archive_records import DIAGNOSTICS as DIAGNOSTICS
from .router_archive_records import FILES as FILES
from .router_archive_records import IDENTITY as IDENTITY
from .router_archive_records import PRESERVED as PRESERVED

# Compatibility imports retain the historical module entry; migrate imports to router_archive_records.
from .router_archive_records import PROTOCOL as PROTOCOL
from .router_archive_records import SEAL_PROTOCOL as SEAL_PROTOCOL
from .router_archive_records import _atomic_json as _atomic_json
from .router_archive_records import _compressed as _compressed
from .router_archive_records import _directory as _directory
from .router_archive_records import _json as _json
from .router_archive_records import _manifest as _manifest
from .router_archive_records import _save_manifest as _save_manifest
from .router_archive_records import _source as _source
from .router_archive_records import _stamp as _stamp
from .router_archive_records import _validate_archive as _validate_archive
from .router_archive_records import _verify_stream as _verify_stream
from .router_archive_records import archived_journal as archived_journal
from .router_archive_records import requires_reconciliation as _pinned
from .router_archive_records import validate_retirement
from .router_journal import load_records, record_digest

DEFAULT_MAX_BYTES = 512 * 1024 * 1024
DEFAULT_RETENTION_DAYS = 30


def capture_archive_owner(instance_id, *, allow_self=False):
    """Attest the native host against Linux ancestry, never from a caller's PID alone."""
    from ..service.background_watchdog import boot_id, process_identity

    if not re.fullmatch(r"v[1-9][0-9]*", instance_id):
        return None
    expected = int(instance_id[1:])
    pid = os.getpid() if allow_self else os.getppid()
    try:
        for _ in range(64):
            info = process_identity(pid)
            if pid == expected:
                return dict(hostname=socket.gethostname(), boot_id=boot_id(), process=info)
            fields = Path("/proc", str(pid), "stat").read_text().rsplit(")", 1)[1].split()
            pid = int(fields[1])
            if pid <= 0:
                break
    except (OSError, ValueError, IndexError):
        pass
    return None


def _host_gone(owner):
    from ..service.background_watchdog import boot_id, process_identity

    if not isinstance(owner, dict) or owner.get("hostname") != socket.gethostname():
        return False
    process = owner.get("process", {})
    if (
        not isinstance(process, dict)
        or not isinstance(owner.get("boot_id"), str)
        or not owner["boot_id"]
        or type(process.get("pid")) is not int
        or process["pid"] < 1
        or not isinstance(process.get("start"), str)
        or not process["start"].isdigit()
    ):
        return False
    try:
        if owner["boot_id"] != boot_id():
            return True
        current = process_identity(process["pid"])
        return current["start"] != process["start"] or current["state"] == "Z"
    except FileNotFoundError:
        return True
    except (OSError, ValueError, IndexError):
        return False


def seal_generation(path, identity, *, publish=True):
    """Called after local writers stop, while the instance lifetime lock is held."""
    files = {}
    for name in FILES:
        try:
            fd = open_private(_source(path, name), os.O_RDONLY)
        except FileNotFoundError:
            if name == "requests.jsonl":
                raise
            continue
        try:
            files[name] = _stamp(os.fstat(fd))
        finally:
            os.close(fd)
    record = dict(
        protocol=SEAL_PROTOCOL,
        hostname=socket.gethostname(),
        identity=identity,
        retired_at=time.time(),
        files=files,
    )
    record["sha256"] = record_digest(record)
    if publish:
        _atomic_json(path.with_suffix(".retired.json"), record)
    record.pop("sha256")
    return record


def _compress(source, destination):
    temporary = destination.with_name(destination.name + ".part")
    size, digest = 0, hashlib.sha256()
    try:
        with (
            os.fdopen(open_private(source, os.O_RDONLY), "rb") as src,
            os.fdopen(open_private(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY), "wb") as raw,
        ):
            before = _stamp(os.fstat(src.fileno()))
            with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as out:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    digest.update(chunk)
                    out.write(chunk)
            raw.flush()
            os.fsync(raw.fileno())
            if before != _stamp(os.fstat(src.fileno())):
                raise ValueError("Sealed source changed during compression")
        os.replace(temporary, destination)
        return dict(size=size, sha256=digest.hexdigest(), status="retained")
    finally:
        temporary.unlink(missing_ok=True)


def _check_seal(path, identity):
    return validate_retirement(_json(path.with_suffix(".retired.json")), identity,
                               hostname=socket.gethostname())


def _check_sources(path, seal, *, allow_missing=False):
    for name in FILES:
        try:
            fd = open_private(_source(path, name), os.O_RDONLY)
        except FileNotFoundError:
            if name in seal["files"] and not allow_missing:
                raise ValueError("Sealed log is missing") from None
            continue
        try:
            if seal["files"].get(name) != _stamp(os.fstat(fd)):
                raise ValueError("Sealed log changed or appeared after retirement")
        finally:
            os.close(fd)


def _remove_sources(path, seal, manifest):
    _check_sources(path, seal, allow_missing=True)
    for name in manifest["files"]:
        _source(path, name).unlink(missing_ok=True)
    sync_directory(path.with_suffix(".state"))
    sync_directory(path.parent)


def _archive(path, identity, seal, apply):
    state = path.with_suffix(".state")
    directory = state / "archive"
    if (directory / "manifest.json").exists():
        manifest, rows = _validate_archive(directory, identity)
        if set(manifest["files"]) != set(seal["files"]):
            raise ValueError("Archive inventory differs from retirement seal")
        _check_sources(path, seal, allow_missing=True)
        if apply:
            _remove_sources(path, seal, manifest)
            _finish_pruning(directory, manifest)
        return manifest, rows, "archived"
    _check_sources(path, seal)
    with os.fdopen(open_private(_source(path, "requests.jsonl"), os.O_RDONLY), "rb") as stream:
        rows, _, _ = load_records(stream, identity)
    manifest = dict(
        protocol=PROTOCOL,
        identity=identity,
        retired_at=seal["retired_at"],
        archived_at=time.time(),
        pinned=_pinned(rows),
        files={},
    )
    if not apply:
        return manifest, rows, "would_archive"
    private_dir(directory)
    # Files left by an interrupted pre-publication copy are disposable; originals remain.
    for name in seal["files"]:
        for suffix in (".gz", ".gz.part"):
            candidate = directory / (name + suffix)
            if candidate.exists() or candidate.is_symlink():
                fd = open_private(candidate, os.O_RDONLY)
                os.close(fd)
                candidate.unlink()
        manifest["files"][name] = _compress(_source(path, name), directory / (name + ".gz"))
    _check_sources(path, seal)
    _save_manifest(directory, manifest)
    _validate_archive(directory, identity)
    _remove_sources(path, seal, manifest)
    return manifest, rows, "archived"


def _finish_pruning(directory, manifest):
    for name, item in manifest["files"].items():
        if item["status"] == "pruned":
            (directory / (name + ".gz")).unlink(missing_ok=True)
    sync_directory(directory)


def _usage(root):
    """Count only this bridge directory, without traversing links or other journals."""
    total = 0
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = [name for name in names if not Path(directory, name).is_symlink()]
        for name in files:
            try:
                info = Path(directory, name).lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
    return total


def _generation_bytes(path):
    total = 0
    for item in path.parent.glob(path.stem + ".*"):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISREG(info.st_mode):
            total += info.st_size
        elif stat.S_ISDIR(info.st_mode):
            total += _usage(item)
    return total


def _identity(path):
    descriptor = _json(path)
    if descriptor.get("protocol") != "cad_ai_instance_bridge.v1":
        raise ValueError("Invalid bridge descriptor")
    identity = {key: identifier(descriptor[key]) for key in IDENTITY}
    key = json.dumps([socket.gethostname(), identity["instance_id"], identity["generation"]])
    if path.stem != hashlib.sha256(key.encode()).hexdigest():
        raise ValueError("Bridge belongs to another host or has an invalid discovery key")
    return identity


def maintain_router_logs(
    launch_dir, *, apply=False, retention_days=DEFAULT_RETENTION_DAYS, max_bytes=DEFAULT_MAX_BYTES
):
    """Compress sealed logs and expire optional diagnostics, reporting any protected excess."""
    if (
        type(retention_days) is not int
        or retention_days < 0
        or retention_days > 36500
        or type(max_bytes) is not int
        or max_bytes < 1
    ):
        raise ValueError("Invalid router retention policy")
    report = dict(
        protocol=PROTOCOL,
        dry_run=not apply,
        retention_days=retention_days,
        max_bytes=max_bytes,
        generations=[],
        errors=[],
        pruned_files=[],
        would_prune_files=[],
        reclaimable_diagnostic_bytes=0,
        bytes_before=0,
        bytes_after=0,
        over_budget=False,
    )
    root = agent_root(launch_dir) / "bridges"
    if not root.exists():
        return report
    for parent in (root.parent.parent.parent, root.parent.parent):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError("Invalid bridge parent directory")
    _directory(root.parent)
    _directory(root)
    report["bytes_before"] = _usage(root)
    candidates = []
    for path in sorted(root.glob("*.json")):
        if not re.fullmatch("[0-9a-f]{64}", path.stem):
            continue
        result = dict(key=path.stem, status="invalid")
        report["generations"].append(result)
        try:
            identity = _identity(path)
            result.update(identity)
            with ExitStack() as stack:
                fd = open_private(path.with_suffix(".lock"), os.O_RDWR)
                stack.callback(os.close, fd)
                try:
                    state_lock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    result["status"] = "active"
                    continue
                seal = None
                owner = _json(path).get("archive_owner")
                if owner and not _host_gone(owner):
                    result["status"] = "host_active"
                    continue
                if path.with_suffix(".retired.json").exists():
                    seal = _check_seal(path, identity)
                else:
                    if not _host_gone(owner):
                        result["status"] = "host_active" if owner else "unsealed"
                        continue
                state = path.with_suffix(".state")
                _directory(state)
                for name, busy in (
                    ("maintenance.lock", "reader_active"),
                    ("writer.lock", "writer_active"),
                ):
                    fd = open_private(state / name, os.O_RDWR)
                    stack.callback(os.close, fd)
                    try:
                        state_lock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        result["status"] = busy
                        break
                else:
                    if seal is None:
                        # Validate before publishing a crash-retirement seal.
                        with os.fdopen(
                            open_private(state / "requests.jsonl", os.O_RDONLY), "rb"
                        ) as stream:
                            load_records(stream, identity)
                        seal = seal_generation(path, identity, publish=apply)
                    manifest, rows, result["status"] = _archive(path, identity, seal, apply)
                    result["pinned"] = _pinned(rows)
                    if not result["pinned"]:
                        for name in DIAGNOSTICS:
                            item = manifest["files"].get(name)
                            candidate = (
                                state / "archive" / (name + ".gz") if item else _source(path, name)
                            )
                            if item and item["status"] != "retained":
                                continue
                            if not candidate.exists():
                                continue
                            size = candidate.stat().st_size
                            report["reclaimable_diagnostic_bytes"] += size
                            expired = time.time() - seal["retired_at"] >= retention_days * 86400
                            if not apply and (expired or report["bytes_before"] > max_bytes):
                                report["would_prune_files"].append(
                                    dict(
                                        key=path.stem,
                                        generation=identity["generation"],
                                        file=name,
                                        bytes=size,
                                        reason="retention" if expired else "capacity_candidate",
                                    )
                                )
                    if apply and not result["pinned"]:
                        candidates.append((manifest["retired_at"], path, identity))
        except (OSError, ValueError, KeyError, TypeError, EOFError) as exc:
            result["status"] = "error"
            result["error"] = str(exc)
            report["errors"].append(dict(key=path.stem, error=str(exc)))
    # Reacquire all lifetime/read locks before a separate pruning pass.
    for retired_at, path, identity in sorted(candidates, key=lambda item: item[0]):
        try:
            with ExitStack() as stack:
                for lock in (
                    path.with_suffix(".lock"),
                    path.with_suffix(".state") / "writer.lock",
                    path.with_suffix(".state") / "maintenance.lock",
                ):
                    fd = open_private(lock, os.O_RDWR)
                    stack.callback(os.close, fd)
                    state_lock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                directory = path.with_suffix(".state") / "archive"
                manifest, rows = _validate_archive(directory, identity)
                if _pinned(rows):
                    continue
                for name in DIAGNOSTICS:
                    item = manifest["files"].get(name)
                    if not item or item["status"] != "retained":
                        continue
                    expired = time.time() - retired_at >= retention_days * 86400
                    if not expired and _usage(root) <= max_bytes:
                        continue
                    item.update(
                        status="pruned",
                        pruned_at=time.time(),
                        prune_reason="retention" if expired else "capacity",
                    )
                    _save_manifest(directory, manifest)
                    _finish_pruning(directory, manifest)
                    report["pruned_files"].append(
                        dict(
                            generation=identity["generation"],
                            key=path.stem,
                            file=name,
                            reason=item["prune_reason"],
                        )
                    )
        except BlockingIOError:
            continue
        except (OSError, ValueError, KeyError, TypeError, EOFError) as exc:
            report["errors"].append(dict(key=path.stem, error=str(exc)))
    report["bytes_after"] = _usage(root)
    for row in report["generations"]:
        row["bytes"] = _generation_bytes(root / (row["key"] + ".json"))
    report["over_budget"] = report["bytes_after"] > max_bytes
    return report
