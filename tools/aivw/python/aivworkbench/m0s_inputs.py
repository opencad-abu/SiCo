"""M0-S source snapshots, state staging, and config parsing."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import shutil
from typing import Iterable

from .errors import EnvironmentError
from .workspace import sha256_file


_DESIGN = re.compile(r"^design\s+(?P<lib>[^.\s]+)\.(?P<cell>[^:\s]+):(?P<view>\S+)\s*;$")
_LIST = re.compile(r"^(?P<kind>liblist|viewlist|stoplist)\s+(?P<values>.+?)\s*;$")
_CELL = re.compile(
    r"^cell\s+(?P<lib>[^.\s]+)\.(?P<cell>\S+)\s+binding\s+:(?P<view>\S+)\s*;$"
)
_INSTANCE = re.compile(
    r"^inst\s+\((?P<parent_lib>[^.\s]+)\.(?P<parent_cell>[^:]+):(?P<parent_view>[^)]+)\)"
    r"\.(?P<instance>\S+)\s+binding\s+:(?P<view>\S+)\s*;$"
)


def file_identity(path: Path) -> dict[str, object]:
    stat = path.stat()
    return {
        "path": str(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": sha256_file(path),
    }


def tree_identity(roots: Iterable[Path]) -> dict[str, object]:
    records: list[tuple[str, int, int, str]] = []
    byte_count = 0
    root_values = tuple(Path(root) for root in roots)
    for root in root_values:
        if not root.is_dir():
            raise EnvironmentError(f"required M0-S source directory is missing: {root}")
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            stat = path.stat()
            relative = f"{root.name}/{path.relative_to(root).as_posix()}"
            records.append((relative, stat.st_size, stat.st_mtime_ns, sha256_file(path)))
            byte_count += stat.st_size
    digest = hashlib.sha256()
    for record in records:
        digest.update(repr(record).encode("utf-8"))
        digest.update(b"\0")
    return {
        "roots": [str(root) for root in root_values],
        "file_count": len(records),
        "byte_count": byte_count,
        "identity_sha256": digest.hexdigest(),
    }


def stage_state(
    source: Path,
    state_root: Path,
    *,
    library: str,
    cell: str,
    simulator: str,
    state_name: str,
) -> dict[str, object]:
    if not source.is_dir():
        raise EnvironmentError(f"required ADE state directory is missing: {source}")
    destination = state_root / library / cell / simulator / state_name
    destination.parent.mkdir(parents=True)
    shutil.copytree(source, destination, copy_function=shutil.copy2)
    before = tree_identity((source,))
    staged = tree_identity((destination,))
    source_hashes = {
        path.relative_to(source).as_posix(): sha256_file(path)
        for path in sorted(source.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }
    staged_hashes = {
        path.relative_to(destination).as_posix(): sha256_file(path)
        for path in sorted(destination.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }
    if source_hashes != staged_hashes:
        raise EnvironmentError("staged ADE state does not match its source")
    return {
        "source": str(source),
        "destination": str(destination),
        "state_argument": f"{state_root}:{state_name}:{simulator}",
        "source_identity": before,
        "staged_identity": staged,
        "source_policy": "read_only_copy",
    }


def parse_expand_config(path: Path) -> dict[str, object]:
    result: dict[str, object] = {
        "source": file_identity(path),
        "config_name": "",
        "top": {},
        "liblist": [],
        "viewlist": [],
        "stoplist": [],
        "cell_bindings": [],
        "instance_bindings": [],
    }
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("//") or line.startswith("#"):
            continue
        if line.startswith("config ") and line.endswith(";"):
            result["config_name"] = line[7:-1].strip()
            continue
        match = _DESIGN.fullmatch(line)
        if match:
            result["top"] = {
                "library": match.group("lib"),
                "cell": match.group("cell"),
                "view": match.group("view"),
            }
            continue
        match = _LIST.fullmatch(line)
        if match:
            values = [item.strip(" \")") for item in match.group("values").split(",")]
            result[match.group("kind")] = [item for item in values if item]
            continue
        match = _CELL.fullmatch(line)
        if match:
            result["cell_bindings"].append(match.groupdict())  # type: ignore[union-attr]
            continue
        match = _INSTANCE.fullmatch(line)
        if match:
            result["instance_bindings"].append(match.groupdict())  # type: ignore[union-attr]
    if not result["config_name"] or not result["top"]:
        raise EnvironmentError(f"cannot parse M0-S config identity: {path}")
    if not result["liblist"] or not result["viewlist"] or not result["stoplist"]:
        raise EnvironmentError(f"cannot parse M0-S config defaults: {path}")
    return result
