"""Assemble scoped blocks and validate each Spectre library section separately."""
from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import PurePosixPath
import re
from typing import Sequence

from .errors import RequestValidationError
from .model import CornerExport, MTS_DESIGN_CIRCUIT_SECTION, validate_oa_name
from .scoper.core import ScopeResult, _ports, _subckt_parts
from .scoper.lexer import ScopeError, logical_statements

_DIRECTIVE = re.compile(r"^(library|section|endsection|endlibrary)\s+(\S+)\s*$", re.I)
_OPTIONS = re.compile(r"^\S+\s+options\b", re.I)
_INCLUDE = re.compile(r'^include\s+"((?:\\.|[^"\\])*)"(?:\s+section\s*=\s*(\S+))?\s*$', re.I)
_TEMP = re.compile(r'''(?<![\w$])temp\s*=\s*(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s]+)''', re.I)


def inherit_temperature(result: ScopeResult) -> ScopeResult:
    """Remove generated local temp assignments, including continued options.

    This leaves tnom and device temperature offsets alone. Included PDK contents
    are not rewritten; their own explicit local temperature overrides still apply.
    """
    lines = []
    dropped = list(result.dropped)
    for statement in logical_statements(result.output, "spectre"):
        parsed = statement.parsed("spectre")
        if _OPTIONS.match(parsed) and _TEMP.search(parsed):
            filtered = _TEMP.sub("", parsed).rstrip()
            dropped.append(f"line {statement.start_line}: inherited temperature removed local temp")
            if _OPTIONS.sub("", filtered).strip():
                lines.append(filtered)
        else:
            lines.append(statement.text)
    output = "\n".join(lines).rstrip() + "\n"
    return replace(result, output=output, dropped=tuple(dropped), scoped_sha256=hashlib.sha256(output.encode()).hexdigest())


def block_ports(text: str, top: str) -> tuple[str, ...]:
    statements = logical_statements(text, "spectre")
    if any(_DIRECTIVE.match(s.parsed("spectre")) for s in statements):
        raise ScopeError("library directives are not allowed inside a corner block")
    definitions, outside = _subckt_parts(statements, "spectre")
    tops = [d for d in definitions if d.name == top]
    if len(tops) != 1:
        raise ScopeError(f"corner must contain exactly one top subckt {top!r}")
    depth = 0
    for s in statements:
        parsed = s.parsed("spectre")
        if re.match(r"subckt\b", parsed, re.I):
            if depth == 0 and not re.match(r"subckt\s+" + re.escape(top) + r"(?:\s|\(|$)", parsed):
                raise ScopeError("corner dependency is outside the public top subckt")
            depth += 1
        elif re.match(r"ends\b", parsed, re.I):
            depth -= 1
    for s in outside:
        if s.stripped and not s.is_comment("spectre") and s.parsed("spectre").casefold() != "simulator lang=spectre":
            raise ScopeError(f"corner has an unscoped statement at line {s.start_line}: {s.stripped}")
    return _ports(tops[0].header, "spectre")


def _read_sections(text: str) -> tuple[tuple[str, str], ...]:
    """Read the envelope without treating the design circuit as a full block."""
    sections = []
    library = None
    current = None
    body = []
    closed = False
    for s in logical_statements(text, "spectre"):
        parsed = s.parsed("spectre")
        directive = _DIRECTIVE.fullmatch(parsed)
        if directive:
            kind, name = directive.groups()
            kind = kind.lower()
            validate_oa_name(name, "library/section name")
            if kind == "library" and library is None and not closed:
                library = name
            elif kind == "section" and library and current is None and not closed:
                current, body = name, []
            elif kind == "endsection" and current == name:
                block = "\n".join(body).rstrip() + "\n"
                sections.append((name, block))
                current = None
            elif kind == "endlibrary" and current is None and library == name and not closed:
                closed = True
            else:
                raise ScopeError(f"invalid corner library boundary: {parsed}")
        elif current is not None:
            body.append(s.text)
        elif parsed and not s.is_comment("spectre") and parsed.casefold() != "simulator lang=spectre":
            raise ScopeError(f"statement outside a corner section: {parsed}")
    if not closed or current or not sections:
        raise ScopeError("incomplete or empty corner library")
    names = [name for name, _ in sections]
    if len(names) != len(set(names)):
        raise ScopeError("duplicate corner library sections")
    return tuple(sections)


def _include_parts(parsed: str) -> tuple[str, str] | None:
    match = _INCLUDE.fullmatch(parsed)
    if match:
        return (match.group(1).replace('\\"', '"').replace('\\\\', '\\'), match.group(2) or "")
    return None


def _design_reference(parsed: str, top: str) -> bool:
    include = _include_parts(parsed)
    if include is None:
        return False
    path, section = include
    filename = f"{top}_corners.scs"
    if section == MTS_DESIGN_CIRCUIT_SECTION:
        if path != filename:
            raise ScopeError("design circuit reference must use the current library filename")
        return True
    if PurePosixPath(path) == PurePosixPath(filename):
        raise ScopeError("same-file reference must select the design circuit section")
    return False


def split_library(text: str, top: str) -> tuple[tuple[str, str], ...]:
    """Return complete public blocks, expanding our one-level design reference."""
    raw = _read_sections(text)
    common = dict(raw).get(MTS_DESIGN_CIRCUIT_SECTION)
    if common is not None:
        statements = logical_statements(common, "spectre")
        if not any(s.stripped and not s.is_comment("spectre") for s in statements):
            raise ScopeError("empty design circuit section")
        if any(_design_reference(s.parsed("spectre"), top) for s in statements):
            raise ScopeError("cyclic design circuit reference")
        for s in statements:
            parsed = s.parsed("spectre")
            if re.match(r"(?:include|ahdl_include)\b", parsed, re.I) or _OPTIONS.match(parsed):
                raise ScopeError("design circuit section must not contain model includes or options")
    sections = []
    for name, block in raw:
        if name == MTS_DESIGN_CIRCUIT_SECTION:
            continue
        depth = references = 0
        lines = []
        for s in logical_statements(block, "spectre"):
            parsed = s.parsed("spectre")
            if _design_reference(parsed, top):
                if common is None:
                    raise ScopeError("missing design circuit section")
                if depth != 1:
                    raise ScopeError("design circuit reference must be directly inside the top subckt")
                references += 1
                lines.append(common.rstrip())
            else:
                lines.append(s.text)
            if re.match(r"subckt\b", parsed, re.I):
                depth += 1
            elif re.match(r"ends\b", parsed, re.I):
                depth -= 1
        if common is not None and references != 1:
            raise ScopeError(f"corner {name!r} must reference the design circuit exactly once")
        sections.append((name, "\n".join(lines).rstrip() + "\n"))
    if not sections:
        raise ScopeError("corner library has no public sections")
    ports = [block_ports(block, top) for _, block in sections]
    if any(p != ports[0] for p in ports[1:]):
        raise ScopeError("corner sections have different public port order")
    return tuple(sections)


def _body_parts(block: str, top: str) -> tuple[str, str, str] | None:
    """Separate a contiguous local setup prefix from an unchanged body suffix."""
    block_ports(block, top)
    statements = logical_statements(block, "spectre")
    definitions, _ = _subckt_parts(statements, "spectre")
    definition = next(d for d in definitions if d.name == top)
    start = None
    for s in statements:
        if not definition.header.end_line < s.start_line < definition.end.start_line:
            continue
        parsed = s.parsed("spectre")
        if not parsed or s.is_comment("spectre"):
            continue
        setup = re.match(r"(?:include|ahdl_include)\b", parsed, re.I) or _OPTIONS.match(parsed)
        if setup:
            if start is not None:
                return None
        elif start is None:
            start = s.start_line - 1
    if start is None:
        return None
    lines = block.splitlines(keepends=True)
    end = definition.end.start_line - 1
    return "".join(lines[:start]), "".join(lines[start:end]), "".join(lines[end:])


def _render_library(top: str, sections: Sequence[tuple[str, str]]) -> str:
    lines = ["simulator lang=spectre", f"library {top}_corners"]
    for name, block in sections:
        lines.extend((f"section {name}", block.rstrip(), f"endsection {name}"))
    lines.append(f"endlibrary {top}_corners")
    return "\n".join(lines) + "\n"


def assemble_library(export: CornerExport, top: str, blocks: Sequence[ScopeResult]) -> str:
    validate_oa_name(top, "top subckt")
    if len(blocks) != len(export.profiles):
        raise ScopeError("corner block count differs from profile count")
    sections = []
    for profile, block in zip(export.profiles, blocks):
        if profile.name == MTS_DESIGN_CIRCUIT_SECTION:
            raise RequestValidationError("design circuit section cannot be used as a corner profile")
        if block.top != top or block.dialect != "spectre":
            raise ScopeError("corner top/dialect mismatch")
        sections.append((profile.name, block.output))
    if len(sections) > 1:
        parts = [_body_parts(block, top) for _, block in sections]
        if all(part is not None for part in parts) and len({part[1] for part in parts}) == 1:
            reference = f'include "{top}_corners.scs" section={MTS_DESIGN_CIRCUIT_SECTION}\n'
            sections = [(name, prefix + reference + suffix)
                        for (name, _), (prefix, _, suffix) in zip(sections, parts)]
            sections.append((MTS_DESIGN_CIRCUIT_SECTION, parts[0][1]))
    output = _render_library(top, sections)
    validate_library(output, export, top)
    return output


def validate_library(text: str, export: CornerExport, top: str) -> tuple[str, ...]:
    sections = split_library(text, top)
    if tuple(name for name, _ in sections) != tuple(p.name for p in export.profiles):
        raise RequestValidationError("library sections do not match requested corner profiles")
    # Verify the explicit bundle really reached each scoped result in order.
    for profile, (_, block) in zip(export.profiles, sections):
        includes = []
        for s in logical_statements(block, "spectre"):
            include = _include_parts(s.parsed("spectre"))
            if include:
                includes.append(include)
        expected = [(str(m.file), m.section) for m in profile.models if m.enabled]
        # Additional includes may be circuit/Verilog-A dependencies. Require
        # requested entries exactly once and in the requested relative order.
        requested = [pair for pair in includes if pair in expected]
        if requested != expected:
            raise RequestValidationError(f"corner {profile.name!r} model bundle does not match its netlist")
    return block_ports(sections[0][1], top)


def rename_library(text: str, export: CornerExport, source: str, target: str) -> str:
    from .publish import rename_top_netlist
    validate_oa_name(target, "target subckt")
    validate_library(text, export, source)
    sections = []
    for name, block in _read_sections(text):
        if name != MTS_DESIGN_CIRCUIT_SECTION:
            lines = [f'include "{target}_corners.scs" section={MTS_DESIGN_CIRCUIT_SECTION}'
                     if _design_reference(s.parsed("spectre"), source) else s.text
                     for s in logical_statements(block, "spectre")]
            block = rename_top_netlist("\n".join(lines) + "\n", "spectre", source, target)
        sections.append((name, block))
    result = _render_library(target, sections)
    validate_library(result, export, target)
    return result
