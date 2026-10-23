"""Strict bounded JSON, RFC 8785 identity, and safe package-relative I/O."""

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path, PurePosixPath

from ..template_schema import TemplateError

VERSION = "0.1.0"
LIMIT = 16384
TARGET = 8192


def fail(message):
    raise TemplateError("circuit template standard: " + message)


def sha(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def check(value, depth=0):
    if depth > 32:
        fail("JSON nesting exceeds 32")
    if value is None:
        fail("null is not a standard fact state")
    if isinstance(value, dict):
        for key, val in value.items():
            if not isinstance(key, str):
                fail("object keys must be strings")
            check(val, depth + 1)
    elif isinstance(value, list):
        for val in value:
            check(val, depth + 1)
    elif type(value) is int and abs(value) > 2**53 - 1:
        fail("integer exceeds JCS exact range")
    elif type(value) is float and (
        not math.isfinite(value) or (value == 0 and math.copysign(1, value) < 0)
    ):
        fail("non-finite number or negative zero")


def encode(value):
    check(value)
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")


def jcs(value):
    check(value)
    try:
        import rfc8785
    except ImportError as exc:
        raise TemplateError("Install declared dependency rfc8785==0.1.4") from exc
    try:
        return rfc8785.dumps(value)
    except rfc8785.CanonicalizationError as exc:
        fail("invalid JCS value: " + str(exc))


def logical(value):
    return "sha256-jcs:" + hashlib.sha256(jcs(value)).hexdigest()


def relative(root, name):
    if not isinstance(name, str) or not name or "\\" in name:
        fail("invalid relative path")
    path = PurePosixPath(name)
    if path.is_absolute() or any(p in {"..", ".", ""} for p in name.split("/")):
        fail("path escapes package root")
    base = Path(root).resolve()
    result = base.joinpath(*path.parts)
    if base not in result.resolve().parents:
        fail("symlink escapes package root")
    return result


def pairs(rows):
    result = {}
    for key, val in rows:
        if key in result:
            fail("duplicate JSON key: " + key)
        result[key] = val
    return result


def read(path, expected=None):
    with Path(path).open("rb") as stream:
        before = os.fstat(stream.fileno())
        raw = stream.read(LIMIT + 1)
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        fail("file changed during read")
    if len(raw) > LIMIT:
        fail("JSON exceeds 16 KiB")
    if expected and sha(raw) != expected:
        fail("file digest mismatch: " + str(path))
    value = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=pairs,
        parse_constant=lambda _: fail("non-finite JSON"),
    )
    if not isinstance(value, dict):
        fail("document must be an object")
    check(value)
    return value, sha(raw)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic(path, value):
    raw = encode(value)
    if len(raw) > LIMIT:
        fail("JSON exceeds 16 KiB")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temp = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        sync_directory(path.parent)
    finally:
        if temp and temp.exists():
            temp.unlink()
    return sha(raw)
