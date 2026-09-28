"""Bounded rule dependency reads, fingerprints and static INCLUDE expansion."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from .rule_syntax import command_words, logical_lines, strip_comments


_MAX_RULE_FILE_BYTES = 512 * 1024 * 1024
_MAX_RULE_DEPENDENCIES = 4096
_MAX_RULE_INCLUDE_DEPTH = 256
_TCL_ENV_REFERENCE = re.compile(
    r"\$(?:(?:::)?env\((?P<plain>[A-Za-z_][A-Za-z0-9_]*)\)"
    r"|\{(?:::)?env\((?P<braced>[A-Za-z_][A-Za-z0-9_]*)\)\})"
)
_SVRF_ENV_REFERENCE = re.compile(
    r"\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}"
    r"|(?P<plain>[A-Za-z_][A-Za-z0-9_]*))"
)


@dataclass(frozen=True)
class RuleSources:
    texts: dict[Path, str | None]
    fingerprint: str


def read_rule_sources(path: Path) -> RuleSources:
    """Read statically resolvable rule dependencies within a bounded budget."""
    run_dir = path.parent
    texts: dict[Path, str | None] = {}
    fingerprints: dict[Path, str | None] = {}
    total_bytes = 0

    pending: list[tuple[Path, bool]] = [(path, True)]
    while pending:
        candidate, required = pending.pop()
        candidate = candidate.resolve()
        if candidate in texts:
            continue
        if len(texts) >= _MAX_RULE_DEPENDENCIES:
            raise ValueError("Too many rule-file dependencies for rule-group discovery")
        if not candidate.is_file():
            if required:
                raise FileNotFoundError(f"Cannot access DRC rule file: {candidate}")
            texts[candidate] = None
            fingerprints[candidate] = None
            continue

        stat = candidate.stat()
        if stat.st_size > _MAX_RULE_FILE_BYTES - total_bytes:
            raise ValueError("DRC rule files are too large for rule-group discovery")
        try:
            data = candidate.read_bytes()
        except OSError:
            if required:
                raise
            texts[candidate] = None
            fingerprints[candidate] = None
            continue
        total_bytes += len(data)
        if total_bytes > _MAX_RULE_FILE_BYTES:
            raise ValueError("DRC rule files are too large for rule-group discovery")
        text = data.decode(errors="ignore")
        texts[candidate] = text
        fingerprints[candidate] = (
            f"{stat.st_size}:{stat.st_mtime_ns}:{hashlib.sha256(data).hexdigest()}"
        )
        dependencies = _dependency_references(text, run_dir)
        pending.extend((dependency, False) for _, dependency in reversed(dependencies))

    fingerprint = hashlib.sha256()
    fingerprint.update(b"drc-rule-dependencies-v1\0")
    for candidate, dependency_fingerprint in fingerprints.items():
        fingerprint.update(str(candidate).encode(errors="surrogateescape"))
        fingerprint.update(b"\0")
        fingerprint.update((dependency_fingerprint or "missing").encode())
        fingerprint.update(b"\0")
    return RuleSources(texts, fingerprint.hexdigest())


def flatten_static_includes(
    path: Path,
    texts: dict[Path, str | None],
    *,
    run_dir: Path | None = None,
    active: frozenset[Path] = frozenset(),
    depth: int = 0,
    remaining_bytes: list[int] | None = None,
) -> str:
    """Inline resolvable SVRF/TVF INCLUDE files at their source positions."""
    if depth > _MAX_RULE_INCLUDE_DEPTH:
        raise ValueError("Rule-file INCLUDE nesting is too deep for discovery")
    if remaining_bytes is None:
        remaining_bytes = [_MAX_RULE_FILE_BYTES]

    def consume(value: str) -> str:
        remaining_bytes[0] -= len(value.encode(errors="ignore"))
        if remaining_bytes[0] < 0:
            raise ValueError("Expanded DRC rule files are too large for discovery")
        return value

    path = path.resolve()
    run_dir = path.parent if run_dir is None else run_dir
    text = texts.get(path)
    if text is None or path in active:
        return ""
    active = active | {path}
    clean_lines = strip_comments(text).splitlines(keepends=True)
    output: list[str] = []
    for original, clean in zip(text.splitlines(keepends=True), clean_lines):
        output.append(consume(original))
        reference = _dependency_reference(clean, run_dir)
        if reference is None or reference[0] == "source":
            continue
        included = flatten_static_includes(
            reference[1],
            texts,
            run_dir=run_dir,
            active=active,
            depth=depth + 1,
            remaining_bytes=remaining_bytes,
        )
        if included:
            if not original.endswith(("\n", "\r")):
                output.append(consume("\n"))
            output.append(included)
            if not included.endswith(("\n", "\r")):
                output.append(consume("\n"))
    return "".join(output)


def _dependency_references(text: str, run_dir: Path) -> list[tuple[str, Path]]:
    references: list[tuple[str, Path]] = []
    for statement in logical_lines(text):
        reference = _dependency_reference(statement, run_dir)
        if reference is not None:
            references.append(reference)
    return references


def _dependency_reference(statement: str, run_dir: Path) -> tuple[str, Path] | None:
    words = command_words(statement.strip())
    if not words:
        return None
    command = words[0]
    if command.casefold() in {"include", "tvf::include"}:
        if len(words) != 2:
            return None
        syntax = "svrf" if command.casefold() == "include" else "tcl"
        resolved = _resolve_dependency_path(words[1], run_dir, syntax=syntax)
        return (command.casefold(), resolved) if resolved is not None else None
    if command != "source":
        return None
    if len(words) == 2:
        path_word = words[1]
    elif len(words) == 4 and words[1] == "-encoding":
        path_word = words[3]
    else:
        return None
    resolved = _resolve_dependency_path(path_word, run_dir, syntax="tcl")
    return ("source", resolved) if resolved is not None else None


def _resolve_dependency_path(token: str, run_dir: Path, *, syntax: str) -> Path | None:
    if not token or "\0" in token or "\n" in token or "[" in token or "]" in token:
        return None
    pattern = _SVRF_ENV_REFERENCE if syntax == "svrf" else _TCL_ENV_REFERENCE
    unresolved = False

    def replace(match: re.Match[str]) -> str:
        nonlocal unresolved
        name = match.group("plain") or match.group("braced")
        value = os.environ.get(name)
        if value is None:
            unresolved = True
            return ""
        return value

    remaining = pattern.sub("", token)
    if "$" in remaining or unresolved:
        return None
    expanded = pattern.sub(replace, token)
    if unresolved or not expanded:
        return None
    candidate = Path(expanded).expanduser()
    if not candidate.is_absolute():
        candidate = run_dir / candidate
    try:
        return candidate.resolve()
    except (OSError, RuntimeError):
        return None
