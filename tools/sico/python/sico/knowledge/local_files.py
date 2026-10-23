"""Enumerate and open regular documents beneath one explicitly selected root."""

import fnmatch
import os
from pathlib import Path
import stat

from .contracts import KnowledgeError, MAX_FILE, MAX_FILES, MAX_TOTAL, checkpoint, digest

FORMATS = {".md": "markdown", ".markdown": "markdown", ".txt": "text", ".text": "text",
           ".rst": "text", ".pdf": "pdf"}


def matches(name, patterns):
    return any(fnmatch.fnmatchcase(name, p) or p.startswith("**/") and
               fnmatch.fnmatchcase(name, p[3:]) for p in patterns)


def selected(source, name):
    return (FORMATS.get(Path(name).suffix.lower()) in source["formats"] and
            (not source["include"] or matches(name, source["include"])) and
            not matches(name, source["exclude"]))


def root(source):
    path = Path(source["path"])
    if path.is_symlink():
        raise KnowledgeError("path_changed", "知识源根不能是符号链接，请选择真实路径")
    if not path.exists():
        raise KnowledgeError("unavailable", "知识源路径不存在")
    return path.resolve()


def read_bytes(source, name):
    base = root(source)
    parts = Path(name).parts
    if not parts or Path(name).is_absolute() or any(p in {".", ".."} for p in parts):
        raise KnowledgeError("invalid_reference", "资料定位不在知识源范围内")
    if base.is_file():
        if name != base.name:
            raise KnowledgeError("invalid_reference", "资料定位不在知识源范围内")
        base = base.parent
    fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        child = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(child, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE:
                raise KnowledgeError("file_limit", "资料不是普通文件或超过 4 MiB")
            data = stream.read(MAX_FILE + 1)
            after = os.fstat(stream.fileno())
        if len(data) > MAX_FILE or (before.st_size, before.st_mtime_ns) != (
                after.st_size, after.st_mtime_ns):
            raise KnowledgeError("source_changed", "读取期间资料发生变化，请重试")
        return data
    finally:
        os.close(fd)


def scan(source, cancelled, deadline):
    base, names, visited = root(source), [], 0
    if base.is_file():
        names = [base.name] if selected(source, base.name) else []
    elif base.is_dir():
        def inaccessible(_error):
            raise KnowledgeError("unavailable", "部分资料目录不可读取")
        for parent, dirs, files in os.walk(base, followlinks=False, onerror=inaccessible):
            checkpoint(cancelled, deadline)
            visited += len(dirs) + len(files)
            if visited > 16384:
                raise KnowledgeError("file_limit", "资料范围过大，请缩小目录或包含范围")
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and
                             not (Path(parent) / d).is_symlink() and
                             not matches((Path(parent) / d).relative_to(base).as_posix() + "/",
                                         source["exclude"]))
            for name in sorted(files):
                path = Path(parent) / name
                relative = path.relative_to(base).as_posix()
                if not name.startswith(".") and not path.is_symlink() and selected(source, relative):
                    names.append(relative)
            if len(names) > MAX_FILES:
                raise KnowledgeError("file_limit", "一个本地知识源最多 2048 个资料文件")
    else:
        raise KnowledgeError("unavailable", "知识源必须是文件或目录")
    result, total = [], 0
    for name in sorted(names):
        checkpoint(cancelled, deadline)
        data = read_bytes(source, name)
        total += len(data)
        if total > MAX_TOTAL:
            raise KnowledgeError("file_limit", "本地知识源总大小超过 64 MiB")
        result.append((name, digest(data), data))
    return str(base), result
