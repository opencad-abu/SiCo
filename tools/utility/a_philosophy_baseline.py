"""Validate per-file transitional size ceilings and their monotonic history."""

from __future__ import annotations

from pathlib import PurePosixPath


def relative(value):
    if not isinstance(value, str) or not value.strip():
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute() and ".." not in path.parts and path.as_posix() == value
    )


def check_baseline(payload, rows, previous=None):
    errors, warnings, seen = [], [], {}
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return ["unsupported size baseline schema"], [], []
    entries = payload.get("baseline")
    if not isinstance(entries, list):
        return ["size baseline must be a list"], [], []
    for item in entries:
        if not isinstance(item, dict) or not relative(item.get("path")):
            errors.append("baseline requires a relative source path")
            continue
        path = item["path"]
        if path in seen:
            errors.append(f"duplicate size baseline: {path}")
        seen[path] = item
        if type(item.get("lines")) is not int or item["lines"] <= 500:
            errors.append(f"baseline lines must exceed 500: {path}")
        for key in ("owner", "remediation", "exit_condition"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                errors.append(f"baseline {key} must be non-empty: {path}")
        if item.get("rule") != "file-lines":
            errors.append(f"unsupported baseline rule: {path}")
    old = {item["path"]: item for item in previous["baseline"]} if previous else None
    if old is not None:
        for path, item in seen.items():
            if path not in old:
                errors.append(f"new size exemption is forbidden: {path}")
            elif type(item.get("lines")) is int and item["lines"] > old[path]["lines"]:
                errors.append(f"baseline ceiling increased: {path}")
    current = {row.path: row for row in rows}
    for path, item in seen.items():
        row = current.get(path)
        if row is None or row.lines <= 500:
            errors.append(f"remove resolved size baseline: {path}")
        elif row.vendored:
            errors.append(
                f"vendored source belongs outside self-owned baseline: {path}"
            )
        elif row.status != "tracked":
            errors.append(f"new source cannot receive a size exemption: {path}")
    hard = []
    for row in rows:
        if row.vendored or row.lines <= 500:
            continue
        hard.append(row.record())
        item = seen.get(row.path)
        if row.status != "tracked" or item is None:
            errors.append(
                f"unbaselined file exceeds 500 lines: {row.path} ({row.lines}; {row.status})"
            )
        elif type(item.get("lines")) is int:
            if row.lines > item["lines"]:
                errors.append(
                    f"baseline regressed: {row.path} ({row.lines} > {item['lines']})"
                )
            elif row.lines < item["lines"]:
                errors.append(f"lower baseline ceiling to {row.lines}: {row.path}")
    for row in rows:
        if 300 < row.lines <= 500 and not row.vendored:
            warnings.append(f"file exceeds soft limit: {row.path} ({row.lines})")
    return errors, warnings, hard
