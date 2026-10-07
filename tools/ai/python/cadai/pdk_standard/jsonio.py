"""Bounded JSON and content-checked, package-relative mapping shards."""

import hashlib
import json
import math
import re
import os
import tempfile
from pathlib import Path, PurePosixPath

from ..pdk_errors import PdkUnavailable

LIMIT = 16 * 1024
TARGET = 8 * 1024


def fail(message, code="invalid_pdk_standard"):
    raise PdkUnavailable(code, message)


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def sha(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def fingerprint(value):
    # Versioned source projection, deliberately not advertised as RFC 8785 JCS.
    return sha(json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode())


def relative(root, name):
    if not isinstance(name, str) or not name or "\\" in name:
        fail("Invalid package-relative path")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"..", ".", ""} for part in name.split("/")):
        fail("Path escapes package root")
    base = Path(root).resolve()
    result = base.joinpath(*path.parts)
    if base not in result.resolve().parents:
        fail("Path escapes package root")
    return result


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            fail("Duplicate JSON key: " + key)
        result[key] = value
    return result


def _float(value):
    number = float(value)
    if not math.isfinite(number):
        fail("Non-finite JSON")
    return number


def read(path, expected=None):
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(LIMIT + 1)
        if len(raw) > LIMIT:
            fail("Standard JSON exceeds 16 KiB")
        if expected and sha(raw) != expected:
            fail("Standard file digest mismatch: " + str(path))
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_float=_float, parse_constant=lambda _: fail("Non-finite JSON"))
        if not isinstance(value, dict):
            fail("Standard content must be an object")
        return value
    except (OSError, ValueError, UnicodeError) as exc:
        fail("Cannot read standard JSON: " + str(path) + ": " + str(exc))


def atomic(path, value):
    raw = encode(value)
    if len(raw) > LIMIT:
        fail("Standard JSON exceeds 16 KiB")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return sha(raw)


def mapping(root, value, *, depth=0, seen=None):
    if not isinstance(value, dict):
        fail("Mapping required")
    if "$parts" not in value:
        return value
    if set(value) != {"$parts"} or depth >= 4 or not value["$parts"]:
        fail("Invalid shard descriptor")
    seen = set() if seen is None else seen
    result, previous = {}, None
    for part in value["$parts"]:
        if not isinstance(part, dict) or set(part) != {"path", "sha256", "count", "first", "last"}:
            fail("Invalid shard fields")
        if (type(part["count"]) is not int or part["count"] <= 0
                or not isinstance(part["sha256"], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", part["sha256"])):
            fail("Invalid shard count/digest")
        path = relative(root, part["path"])
        if path in seen:
            fail("Repeated or cyclic shard")
        seen.add(path)
        child = mapping(root, read(path, part["sha256"]), depth=depth + 1, seen=seen)
        keys = list(child)
        if (not keys or keys != sorted(keys) or part["count"] != len(keys)
                or part["first"] != keys[0] or part["last"] != keys[-1]
                or (previous is not None and previous >= keys[0])):
            fail("Shard bounds/count/order mismatch")
        previous = keys[-1]
        result.update(child)
        if len(result) > 100000:
            fail("Mapping exceeds 100000 records")
    return result


class Writer:
    def __init__(self, root):
        self.root, self.files = Path(root), {}
        self.serial = 0

    def put(self, name, value, fields=(), *, inventory=True):
        value = dict(value)
        for field in fields:
            if isinstance(value.get(field), dict) and len(encode(value[field])) > TARGET:
                value[field] = self.shard(name, field, value[field], inventory=inventory)
        digest = atomic(relative(self.root, name), value)
        if inventory:
            self.files[name] = digest
        return digest

    def shard(self, name, field, values, *, inventory=True, depth=0):
        if depth >= 4:
            fail("Shard depth exceeds standard limit")
        parts, chunk = [], {}
        for key, value in sorted(values.items()):
            if len(encode({key: value})) > TARGET:
                fail("Indivisible record exceeds 8 KiB: " + key)
            if chunk and len(encode({**chunk, key: value})) > TARGET:
                parts.append(self._part(name, field, chunk, inventory))
                chunk = {}
            chunk[key] = value
        if chunk:
            parts.append(self._part(name, field, chunk, inventory))
        if len(encode({"$parts": parts})) > TARGET:
            grouped = []
            for start in range(0, len(parts), 12):
                rows = parts[start:start + 12]
                self.serial += 1
                path = str(Path(name).with_suffix("")) + f".{field}.{self.serial:04d}.json"
                digest = self.put(path, {"$parts": rows}, inventory=inventory)
                grouped.append(dict(path=path, sha256=digest, count=sum(p["count"] for p in rows),
                                    first=rows[0]["first"], last=rows[-1]["last"]))
            parts = grouped
            if len(encode({"$parts": parts})) > TARGET:
                fail("Shard directory exceeds supported package capacity")
        return {"$parts": parts}

    def _part(self, name, field, chunk, inventory):
        self.serial += 1
        path = str(Path(name).with_suffix("")) + f".{field}.{self.serial:04d}.json"
        digest = self.put(path, chunk, inventory=inventory)
        keys = sorted(chunk)
        return dict(path=path, sha256=digest, count=len(keys), first=keys[0], last=keys[-1])
