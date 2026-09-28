"""Durable, byte-verified copies into a private migration transaction."""

import hashlib
import json
import os
import stat
from pathlib import Path

from ..storage.journal import open_private, private_dir, sync_directory
from .filesystem import fingerprint


def directory(root, relative=""):
    path = Path(root)
    private_dir(path)
    for part in Path(relative).parts:
        if part in {".", ".."}:
            raise ValueError("Invalid migration destination")
        child = path / part
        private_dir(child)
        sync_directory(path)
        path = child
    return path


def verify(path, entry):
    with os.fdopen(open_private(path, os.O_RDONLY), "rb") as stream:
        info = os.fstat(stream.fileno())
        if info.st_nlink != 1 or info.st_size != entry["identity"]["size"]:
            raise ValueError("Migration copy identity mismatch")
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        if digest.hexdigest() != entry["sha256"]:
            raise ValueError("Migration copy hash mismatch")


def copy_entry(tree, entry, destination):
    relative = Path(entry["path"])
    parent = directory(destination, str(relative.parent))
    path = parent / relative.name
    temporary = parent / (".copy-" + hashlib.sha256(str(relative).encode()).hexdigest())
    discard_partial(temporary, path)
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    else:
        verify(path, entry)
        return
    try:
        with tree.open(entry["path"]) as source:
            if fingerprint(os.fstat(source.fileno())) != entry["identity"]:
                raise ValueError("Migration source changed")
            remaining = entry["identity"]["size"]
            with os.fdopen(open_private(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY),
                           "wb") as target:
                while remaining:
                    chunk = source.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        raise ValueError("Migration source truncated")
                    target.write(chunk)
                    remaining -= len(chunk)
                target.flush()
                os.fsync(target.fileno())
            if fingerprint(os.fstat(source.fileno())) != entry["identity"]:
                raise ValueError("Migration source changed")
        verify(temporary, entry)
        # Publication is no-clobber; hard links exist only until the private temp is removed.
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
        sync_directory(parent)
    finally:
        temporary.unlink(missing_ok=True)


def discard_partial(temporary, published):
    try:
        info = temporary.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("Unsafe interrupted migration copy")
    if info.st_nlink != 1:
        target = published.lstat()
        if info.st_nlink != 2 or (info.st_dev, info.st_ino) != (target.st_dev, target.st_ino):
            raise ValueError("Interrupted copy has unexpected hard links")
    temporary.unlink()
    sync_directory(temporary.parent)


def save_document(path, value):
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    transaction = path.parent
    temporary = transaction / ("." + path.name + "-copy")
    discard_partial(temporary, path)
    try:
        fd = open_private(path, os.O_RDONLY)
    except FileNotFoundError:
        with os.fdopen(open_private(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY), "wb") as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
        sync_directory(transaction)
    else:
        with os.fdopen(fd, "rb") as source:
            if os.fstat(source.fileno()).st_nlink != 1 or source.read(len(raw) + 1) != raw:
                raise ValueError("Migration inventory differs from saved transaction")


def verify_layout(root, expected_files, expected_directories):
    """Retries cannot silently include an interrupted or externally injected file."""
    seen_files, seen_directories = set(), set()
    for parent, directories, files in os.walk(root, followlinks=False):
        relative = Path(parent).relative_to(root)
        for name in directories:
            path = Path(parent) / name
            private_dir(path)
            seen_directories.add((relative / name).as_posix())
        for name in files:
            seen_files.add((relative / name).as_posix())
    if seen_files != expected_files or seen_directories != expected_directories:
        raise ValueError("Migration transaction contains unexpected or missing objects")


def parent_paths(files):
    return {parent.as_posix() for name in files for parent in Path(name).parents
            if parent != Path(".")}
