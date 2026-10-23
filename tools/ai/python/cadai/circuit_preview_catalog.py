"""List and read one workspace's preview products, registered or manual."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .circuit_preview_files import (
    MAX_SVG_BYTES,
    parse_svg,
    preview_directory,
    read_contained_file,
)

MAX_CATALOG_FILES = 200
MANIFEST_FIELDS = frozenset({"path", "media_type", "size_bytes", "sha256"})


@dataclass(frozen=True)
class ArtifactRow:
    """One preview directory file as listed; bytes still belong to the file."""

    name: str
    path: Path
    size: int
    mtime: float
    sha256: str = ""
    target: str = ""
    registered: bool = False
    claimed: bool = False
    notice: str = ""


@dataclass(frozen=True)
class Catalog:
    rows: tuple
    truncated: bool = False


@dataclass(frozen=True)
class CatalogArtifact:
    name: str
    path: Path
    data: bytes
    digest: str
    registered: bool
    target: str = ""
    notice: str = ""


def _manifest_preview(path):
    """Read only a complete manifest; a partial or foreign file names no preview."""
    try:
        raw = read_contained_file(path.with_suffix(".json"), 65536)
    except (OSError, ValueError):
        return None
    if not raw:
        return None
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(value, dict) or not MANIFEST_FIELDS.issubset(value):
        return None
    if type(value["size_bytes"]) is not int or not 0 < value["size_bytes"] <= MAX_SVG_BYTES:
        return None
    return value


def _listed_row(path, info):
    value = info.st_mtime_ns / 1_000_000_000
    entry = dict(name=path.name, path=path, size=info.st_size, mtime=value)
    manifest = _manifest_preview(path)
    if manifest is None:
        return ArtifactRow(**entry)
    # A complete manifest claims the name; only a matching file confirms it.
    entry["claimed"] = True
    if manifest["size_bytes"] != info.st_size:
        return ArtifactRow(**entry, notice="登记大小与文件不一致，按未登记产物显示。")
    try:
        data = read_contained_file(path, MAX_SVG_BYTES)
    except (OSError, ValueError):
        return ArtifactRow(**entry, notice="预览文件暂不可读，按未登记产物显示。")
    if hashlib.sha256(data).hexdigest() != manifest["sha256"]:
        return ArtifactRow(**entry, notice="登记校验和与文件不一致，按未登记产物显示。")
    return ArtifactRow(**entry, sha256=manifest["sha256"],
                       target=str(manifest.get("target") or ""), registered=True)


def list_preview_artifacts(workspace):
    """List preview names, sizes and times; manual exports are tolerated."""
    try:
        directory = preview_directory(workspace)
        names = sorted(os.listdir(directory))
    except FileNotFoundError:
        # No preview directory yet; a redirect under .sico stays a hard error.
        return Catalog(())
    rows, truncated = [], False
    for name in names:
        if len(rows) >= MAX_CATALOG_FILES:
            truncated = True
            break
        if not name.endswith(".svg"):
            continue
        try:
            info = os.stat(directory / name, follow_symlinks=False)
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode):
            continue
        rows.append(_listed_row(directory / name, info))
    # Newest first; the name keeps one stable order for equal timestamps.
    rows.sort(key=lambda row: (-row.mtime, row.name))
    return Catalog(tuple(rows), truncated)


def read_preview_artifact(workspace, name):
    """Read one listed preview by directory name and verify its digest."""
    if type(name) is not str or not name or name != Path(name).name or not name.endswith(".svg"):
        raise ValueError("Invalid SVG preview name")
    directory = preview_directory(workspace)
    row = next((item for item in list_preview_artifacts(workspace).rows if item.name == name),
               None)
    if row is None:
        raise ValueError("SVG preview is not listed in the preview catalog")
    if not 0 < row.size <= MAX_SVG_BYTES:
        raise ValueError("SVG preview is outside the preview budget")
    raw = read_contained_file(directory / name, MAX_SVG_BYTES)
    if len(raw) != row.size:
        raise ValueError("SVG artifact size changed during read")
    raw = parse_svg(raw)
    digest = hashlib.sha256(raw).hexdigest()
    # A changed byte or size contradicts a complete manifest that claimed the name.
    if row.claimed and digest != row.sha256:
        raise ValueError("SVG preview checksum changed")
    # Manual exports and manifest mismatches keep their bytes and say so.
    notice = "" if row.registered else (row.notice or "该预览产物未登记，显示的是文件当前内容。")
    return CatalogArtifact(name, row.path, raw, digest, row.registered, row.target, notice)
