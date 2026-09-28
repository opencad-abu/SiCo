#!/usr/bin/env python3
"""Check and mechanically normalize Cadence SKILL call syntax."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
from typing import Iterable

if __package__:
    from .skill_syntax import Frame, Token, parse
else:
    from skill_syntax import Frame, Token, parse


ROOT = Path(__file__).resolve().parents[2]
SKILL_ENDINGS = (".il", ".ils", ".il.src")
IGNORED_PARTS = {".git", ".pytest_cache", "__pycache__"}
STRUCTURAL_FIRST_ARGUMENT = {"let", "letrec", "letseq", "prog", "lambda"}
STRUCTURAL_DIRECT_ARGUMENTS = {"declare", "defclass", "defstruct"}
FUNCTION_DEFINITION_FORMS = {"defgeneric", "defmacro", "defmethod", "defun"}
NON_CALL_NAMES = {"nil", "t"}
MAPPING_FUNCTIONS = {"map", "mapc", "mapcan", "mapcar", "mapcon", "maplist"}
INFIX_STARTS = set("+-*/%<>=!&|^:~[")


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    kind: str
    name: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.kind}: {self.name}"


def _child_start(child: Token | Frame) -> int:
    return child.start if isinstance(child, Token) else child.open_token.start


def _direct_argument_index(frame: Frame) -> int | None:
    parent = frame.parent
    if parent is None:
        return None
    try:
        child_index = parent.children.index(frame)
    except ValueError:
        return None
    if parent.algebraic_name is not None:
        return child_index
    return child_index - 1 if parent.first_name is not None else None


def _is_structural_list(frame: Frame) -> bool:
    parent = frame.parent
    if parent is None:
        return False
    operator = parent.operator
    argument = _direct_argument_index(frame)
    if _is_structural_container(frame):
        return True
    if _is_structural_entry(frame):
        return True
    if operator == "case" and argument is not None and argument >= 1:
        return True
    if operator == "cond":
        return True
    return False


def _is_structural_container(frame: Frame) -> bool:
    parent = frame.parent
    if parent is None:
        return False
    operator = parent.operator
    argument = _direct_argument_index(frame)
    if operator in STRUCTURAL_FIRST_ARGUMENT and argument == 0:
        return True
    if operator in STRUCTURAL_DIRECT_ARGUMENTS:
        return True
    if operator == "foreach":
        arguments = parent.children if parent.algebraic_name else parent.children[1:]
        first = arguments[0] if arguments else None
        mapped = isinstance(first, Token) and first.text in MAPPING_FUNCTIONS
        return argument == int(mapped)
    return operator in FUNCTION_DEFINITION_FORMS and argument == 1


def _is_structural_entry(frame: Frame) -> bool:
    parent = frame.parent
    if parent is None:
        return False
    if _is_structural_container(parent):
        return True
    grandparent = parent.parent
    return (
        grandparent is not None
        and grandparent.operator == "procedure"
        and _direct_argument_index(parent) in {0, 1}
    )


def _is_prefix_call(source: str, frame: Frame) -> bool:
    name = frame.first_name
    if (
        name is None
        or name.text in NON_CALL_NAMES
        or frame.quoted
        or frame.algebraic_name is not None
        or _is_structural_list(frame)
    ):
        return False
    if len(frame.children) == 1:
        return True
    next_child = frame.children[1]
    if isinstance(next_child, Token) and next_child.text in INFIX_STARTS:
        return False
    gap = source[name.end : _child_start(next_child)]
    return bool(gap) and (
        any(character.isspace() for character in gap) or ";" in gap or "/*" in gap
    )


def _is_wrapped_algebraic_call(frame: Frame) -> bool:
    if frame.quoted or frame.algebraic_name is not None or len(frame.children) != 2:
        return False
    name, arguments = frame.children
    return (
        isinstance(name, Token)
        and name.kind == "name"
        and isinstance(arguments, Frame)
        and arguments.algebraic_name == name.text
        and name.end == arguments.open_token.start
    )


def analyze(path: Path, source: str | None = None) -> list[Finding]:
    text = path.read_text(encoding="utf-8") if source is None else source
    _, frames = parse(text)
    findings: list[Finding] = []
    for frame in frames:
        if _is_prefix_call(text, frame):
            name = frame.first_name
            assert name is not None
            findings.append(Finding(path, frame.open_token.line, "prefix call", name.text))
        elif _is_wrapped_algebraic_call(frame):
            name = frame.children[0]
            assert isinstance(name, Token)
            findings.append(
                Finding(path, frame.open_token.line, "wrapped algebraic call", name.text)
            )
    return findings


def _gap_replacement(gap: str) -> str:
    if "\n" in gap or ";" in gap or "/*" in gap:
        return "(" + gap
    return "("


def _rewrite_once(source: str) -> str:
    _, frames = parse(source)
    edits: list[tuple[int, int, str]] = []
    for frame in frames:
        if _is_wrapped_algebraic_call(frame):
            if frame.close_token is None:
                raise ValueError(
                    f"unterminated wrapped call at line {frame.open_token.line}"
                )
            edits.append((frame.open_token.start, frame.open_token.end, ""))
            edits.append((frame.close_token.start, frame.close_token.end, ""))
            continue
        if not _is_prefix_call(source, frame):
            continue
        name = frame.first_name
        assert name is not None
        edits.append((frame.open_token.start, frame.open_token.end, ""))
        if len(frame.children) == 1:
            edits.append((name.end, name.end, "("))
        else:
            next_start = _child_start(frame.children[1])
            gap = source[name.end:next_start]
            edits.append((name.end, next_start, _gap_replacement(gap)))
        if name.text == "defun" and len(frame.children) >= 3:
            function_name = frame.children[1]
            arguments = frame.children[2]
            if isinstance(function_name, Token) and isinstance(arguments, Frame):
                edits.append((name.start, name.end, "procedure"))
                gap = source[function_name.end : arguments.open_token.start]
                edits.append(
                    (
                        function_name.end,
                        arguments.open_token.start,
                        _gap_replacement(gap),
                    )
                )
                edits.append((arguments.open_token.start, arguments.open_token.end, ""))

    result = source
    occupied: list[tuple[int, int]] = []
    for start, end, replacement in sorted(edits, key=lambda edit: (edit[0], edit[1]), reverse=True):
        if any(start < used_end and end > used_start for used_start, used_end in occupied):
            raise ValueError(f"overlapping SKILL style edits at {start}:{end}")
        result = result[:start] + replacement + result[end:]
        if start != end:
            occupied.append((start, end))
    return result


def rewrite_source(source: str) -> str:
    result = source
    for _ in range(32):
        rewritten = _rewrite_once(result)
        if rewritten == result:
            return result
        result = rewritten
    raise ValueError("SKILL style rewrite did not converge after 32 passes")


def _is_skill_source(path: Path) -> bool:
    return path.name.endswith(SKILL_ENDINGS)


def _tracked_skill_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "*.il", "*.ils", "*.il.src"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode == 0:
        return [ROOT / line for line in completed.stdout.splitlines() if line]
    return sorted(
        path
        for path in ROOT.rglob("*")
        if _is_skill_source(path)
        and not any(part in IGNORED_PARTS or part.startswith(".build") for part in path.parts)
    )


def _resolve_paths(arguments: Iterable[str]) -> list[Path]:
    values = list(arguments)
    if not values:
        return _tracked_skill_files()
    paths: list[Path] = []
    for value in values:
        path = Path(value)
        if not path.is_absolute():
            path = ROOT / path
        if path.is_dir():
            paths.extend(
                candidate
                for candidate in path.rglob("*")
                if _is_skill_source(candidate)
            )
        else:
            paths.append(path)
    return sorted(set(paths))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--rewrite", action="store_true")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args(argv)
    paths = _resolve_paths(args.paths)

    if args.rewrite:
        for path in paths:
            source = path.read_text(encoding="utf-8")
            rewritten = rewrite_source(source)
            if rewritten != source:
                path.write_text(rewritten, encoding="utf-8")

    findings = [finding for path in paths for finding in analyze(path)]
    for finding in findings:
        display = finding.path.relative_to(ROOT) if finding.path.is_relative_to(ROOT) else finding.path
        print(Finding(display, finding.line, finding.kind, finding.name).render())
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
