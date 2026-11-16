# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""router project files for the opt-in source acceptance."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from router_project_config import LOCK_NAMES


def _file_digest(path):
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1 << 20)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return [size, digest.hexdigest()]

def _tree_digest(roots):
    entries = {}
    for root in roots:
        root = Path(root)
        if root.is_file():
            entries[str(root)] = _file_digest(root)
            continue
        if not root.is_dir():
            raise AssertionError(f"Fingerprint root is missing: {root}")
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            if path.name.endswith(LOCK_NAMES) or path.name.startswith(".lock"):
                continue
            entries[str(path)] = _file_digest(path)
    return entries

def _digest_of(entries):
    digest = hashlib.sha256()
    for path in sorted(entries):
        digest.update(path.encode())
        digest.update(json.dumps(entries[path], separators=(",", ":")).encode())
    return digest.hexdigest()

def _read_fields(path):
    fields = {}
    for line in path.read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key.strip()] = value.strip()
    return fields

def _receipt_core(receipt):
    """Compare the durable receipt without the append-only SKILL stage log."""
    return {key: value for key, value in receipt.items()
            if key not in {"skill_stage", "skill_stages"}}
