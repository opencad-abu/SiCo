"""Small, conservative logical lexer and MTS scope transformer.

The transformer intentionally handles the stable netlist constructs emitted by
Cadence direct/socket netlisters.  Unknown statements are retained in the
report and rejected when they would make scope placement ambiguous.  It is
not a generic SPICE parser and never performs global textual substitutions.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Sequence

from .lexer import ScopeError, Statement, logical_statements as _logical_statements


@dataclass(frozen=True)
class Subckt:
    name: str
    header: Statement
    body: tuple[Statement, ...]
    end: Statement


@dataclass(frozen=True)
class ScopeResult:
    dialect: str
    top: str
    ports: tuple[str, ...]
    output: str
    dependencies: tuple[str, ...]
    dropped: tuple[str, ...]
    warnings: tuple[str, ...]
    raw_sha256: str
    scoped_sha256: str

    def report(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "dialect": self.dialect,
            "top": self.top,
            "ports": list(self.ports),
            "dependencies": list(self.dependencies),
            "dropped": list(self.dropped),
            "warnings": list(self.warnings),
            "raw_sha256": self.raw_sha256,
            "scoped_sha256": self.scoped_sha256,
        }


# Match parsed logical text, where continuation markers have been folded.
# Escaped delimiters belong to identifiers, including bus/array names.
_SPECTRE_TOKEN = r"(?:\\[^\r\n]|[^\s\\(),])+"
_SPECTRE_NODES = r"(?:\\[^\r\n]|[^\\()])*"
_SPECTRE_SUBCKT = re.compile(
    rf"^subckt\s+({_SPECTRE_TOKEN})(?:\s*\(({_SPECTRE_NODES})\)|\s+({_SPECTRE_NODES}))?$",
    re.I,
)
_HSPICE_SUBCKT = re.compile(r"^\.subckt\s+(\S+)(?:\s+(.*?))?\s*$", re.I)
# End markers are structural tokens.  Anchor the whole statement so a
# malformed ``.ends extra tokens`` cannot be silently accepted as a valid
# terminator and so an unmatched end can be diagnosed by the outer parser.
_SPECTRE_ENDS = re.compile(r"^ends(?:\s+(\S+))?\s*$", re.I)
_HSPICE_ENDS = re.compile(r"^\.ends(?:\s+(\S+))?\s*$", re.I)
_SPECTRE_INSTANCE = re.compile(
    rf"^{_SPECTRE_TOKEN}\s*\({_SPECTRE_NODES}\)\s+({_SPECTRE_TOKEN})", re.I
)
_HSPICE_INSTANCE = re.compile(r"^x\S+\s+(.*)$", re.I)

# These fields are owned by ADE's result/runtime machinery rather than by the
# circuit represented by an MTS block.  In particular, retaining a relative
# ``sensfile`` path after moving the scoped deck to ``PROJ_ADE_DB_DIR`` makes
# the generated deck depend on the private OCEAN run directory.  Keep the
# policy explicit and deliberately small; simulator controls such as reltol,
# tolerances, and psfversion remain valid local options.
_SPECTRE_RUNTIME_OPTION_NAMES = frozenset(
    {
        "sensfile",
        "checklimitdest",
        "rawfile",
        "rawfiledir",
        "rawfilepath",
        "rawfiledestination",
        "psfdir",
        "psfpath",
    }
)
_SPECTRE_OPTIONS_HEAD = re.compile(
    r"^\s*(simulatoroptions|scopedoptions)\s+options\b", re.I
)
_SPECTRE_INCLUDE = re.compile(
    r"^\s*(?:include|ahdl_include)\s+(?:\"([^\"]+)\"|(\S+))", re.I
)
_SPECTRE_RUNTIME_ASSIGNMENT = re.compile(
    r"(?<![A-Za-z0-9_$])(?:"
    + "|".join(
        re.escape(name)
        for name in sorted(_SPECTRE_RUNTIME_OPTION_NAMES, key=len, reverse=True)
    )
    + r")\s*=\s*"
    r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s\\]+)",
    re.I,
)


def _subckt_parts(statements: Sequence[Statement], dialect: str) -> tuple[tuple[Subckt, ...], tuple[Statement, ...]]:
    starts = _SPECTRE_SUBCKT if dialect == "spectre" else _HSPICE_SUBCKT
    ends = _SPECTRE_ENDS if dialect == "spectre" else _HSPICE_ENDS
    definitions: list[Subckt] = []
    outside: list[Statement] = []

    def parse_one(index: int) -> tuple[Subckt, int]:
        statement = statements[index]
        match = starts.match(statement.parsed(dialect))
        if not match:
            raise ScopeError(f"internal parser error at line {statement.start_line}")
        name = match.group(1)
        header = statement
        body: list[Statement] = []
        index += 1
        while index < len(statements):
            current = statements[index]
            parsed = current.parsed(dialect)
            if starts.match(parsed):
                nested, index = parse_one(index)
                # Cadence MTS output places dependency definitions inside the
                # selected top block.  Keep them in the global definition
                # table while removing them from the enclosing body.
                definitions.append(nested)
                continue
            end_match = ends.match(parsed)
            if end_match:
                end_name = end_match.group(1)
                if end_name and _name_key(end_name, dialect) != _name_key(name, dialect):
                    raise ScopeError(
                        f"mismatched {dialect} end at line {current.start_line}: "
                        f"subckt {name!r} closed as {end_name!r}"
                    )
                return Subckt(name, header, tuple(body), current), index + 1
            body.append(current)
            index += 1
        raise ScopeError(f"unbalanced {dialect} subckt {name!r}: no end statement")

    index = 0
    while index < len(statements):
        statement = statements[index]
        parsed = statement.parsed(dialect)
        if starts.match(parsed):
            definition, index = parse_one(index)
            definitions.append(definition)
        elif ends.match(parsed):
            raise ScopeError(
                f"unmatched {dialect} end statement at line {statement.start_line}"
            )
        else:
            outside.append(statement)
            index += 1
    # A name collision means the dependency lookup would otherwise select an
    # arbitrary definition (the old dict-comprehension silently chose the
    # last one).  Reject it before any scope transform is attempted.
    seen: dict[str, Subckt] = {}
    for definition in definitions:
        key = _name_key(definition.name, dialect)
        previous = seen.get(key)
        if previous is not None:
            raise ScopeError(
                f"expected exactly one {dialect} subckt definition for "
                f"{definition.name!r}; duplicate definition "
                f"at lines {previous.header.start_line} and {definition.header.start_line}"
            )
        seen[key] = definition
    return tuple(definitions), tuple(outside)


def _name_key(name: str, dialect: str) -> str:
    return name.casefold() if dialect == "hspiceD" else name


def _ports(header: Statement, dialect: str) -> tuple[str, ...]:
    match = (_SPECTRE_SUBCKT if dialect == "spectre" else _HSPICE_SUBCKT).match(header.parsed(dialect))
    if not match:
        return ()
    if dialect == "spectre":
        raw = match.group(2) if match.group(2) is not None else match.group(3) or ""
        return tuple(re.findall(_SPECTRE_TOKEN, raw))
    raw = match.group(2) or ""
    return tuple(token for token in re.split(r"[\s,]+", raw.strip()) if token)


def _dependencies(top: Subckt, definitions: Sequence[Subckt], dialect: str) -> tuple[Subckt, ...]:
    by_name = {_name_key(item.name, dialect): item for item in definitions}
    found: list[Subckt] = []
    seen: set[str] = set()
    pending = list(top.body)
    while pending:
        statement = pending.pop(0).parsed(dialect)
        if not statement or statement.startswith(("//", "*", "#")):
            continue
        match = _SPECTRE_INSTANCE.match(statement) if dialect == "spectre" else _HSPICE_INSTANCE.match(statement)
        if not match:
            continue
        master = match.group(1).split()[-1] if dialect == "hspiceD" else match.group(1)
        dependency = by_name.get(_name_key(master, dialect))
        if dependency is None or _name_key(dependency.name, dialect) in seen:
            continue
        seen.add(_name_key(dependency.name, dialect))
        found.append(dependency)
        pending.extend(dependency.body)
    return tuple(found)


def _known_statement(statement: Statement, dialect: str) -> bool:
    """Conservatively classify statements for parser diagnostics.

    Device and instance syntax is intentionally recognized broadly enough for
    netlister output.  A statement that is not recognized is retained in its
    original scope but reported as a warning; publication can then apply the
    product's simulator-validation policy instead of silently dropping it.
    """

    lowered = statement.stripped.casefold()
    if not lowered or statement.is_comment(dialect):
        return True
    if dialect == "spectre":
        if _SPECTRE_INSTANCE.match(statement.parsed(dialect)):
            return True
        return lowered.startswith(
            (
                "simulator lang=",
                "include ",
                "ahdl_include ",
                "simulatoroptions ",
                "scopedoptions ",
                "parameters ",
                "param ",
                "model ",
                "global ",
                "save ",
                "saveoptions ",
                "modelparameter info",
                "element info",
                "outputparameter info",
                "designparamvals info",
                "primitives info",
                "subckts info",
                "analysis ",
                "tran ",
                "dc ",
                "ac ",
                "noise ",
                "pss ",
                "run()",
            )
        )
    if _HSPICE_INSTANCE.match(statement.stripped):
        return True
    if lowered.startswith((".", "+")):
        # All dot directives are retained or dropped by an explicit policy in
        # ``classify``.  Recognizing them here avoids warning on normal HSPICE
        # controls while still allowing truly bare unknown statements through.
        return True
    # Standard HSPICE primitive/device lines begin with a non-X element name.
    # Keep this deliberately narrow so ``bogus statement`` remains visible as
    # an unknown diagnostic in tests and in production reports.
    return bool(re.match(r"^[mrcdlqjvefghibtuwkz](?:\d|[_$])\S*\s+", statement.stripped, re.I))


def _is_runtime(statement: Statement, dialect: str) -> bool:
    lowered = statement.stripped.casefold()
    if dialect == "spectre":
        return lowered.startswith(("simulator lang=", "save ", "saveoptions", "modelparameter info", "element info", "outputparameter info", "designparamvals info", "primitives info", "subckts info", "analysis ", "tran ", "dc ", "ac ", "noise ", "pss ", "run()"))
    return lowered.startswith((".end", ".print", ".probe", ".measure", ".tran", ".ac", ".dc", ".noise", ".op", ".option post", ".global"))


def _is_spectre_runtime_include(statement: Statement) -> bool:
    """Return whether an include is ADE's private runtime setup file.

    ``ade_e.scs`` is emitted relative to the OCEAN result directory.  It is
    useful while producing PSF data, but is not a model or circuit dependency
    and becomes a dangling include once the stable MTS deck is published.
    Match the basename only so an absolute worker path and the usual relative
    form receive the same treatment; user model files with other names remain
    untouched.
    """

    match = _SPECTRE_INCLUDE.match(statement.stripped)
    if match is None:
        return False
    path = (match.group(1) or match.group(2) or "").replace("\\", "/")
    return path.rsplit("/", 1)[-1].casefold() == "ade_e.scs"


def _filter_spectre_runtime_options(
    statement: Statement,
) -> tuple[Statement | None, tuple[str, ...]]:
    """Remove ADE result-directory fields from a Spectre options statement.

    The logical lexer has already joined continuation lines, so filtering the
    complete statement avoids leaving a dangling continuation when the final
    field on a line is removed.  Only a conservative allowlist of known
    runtime field names is touched; unknown/user simulator options retain
    their original spelling and line wrapping.
    """

    if _SPECTRE_OPTIONS_HEAD.match(statement.stripped) is None:
        return statement, ()
    matches = tuple(
        match.group(0).split("=", 1)[0].strip()
        for match in _SPECTRE_RUNTIME_ASSIGNMENT.finditer(statement.text)
    )
    filtered = _SPECTRE_RUNTIME_ASSIGNMENT.sub("", statement.text)
    if filtered == statement.text:
        return statement, ()

    # If every assignment was runtime-only, discard the directive instead of
    # emitting a syntactically empty ``simulatorOptions options`` statement.
    if not _SPECTRE_OPTIONS_HEAD.sub("", filtered, count=1).replace("\\", "").strip():
        return None, matches

    # Removing the last assignment on a continued line can leave a bare
    # backslash or a continuation marker at EOF.  Trim only whitespace and
    # those now-invalid markers; preserve all other generated formatting.
    lines: list[str] = []
    for line in filtered.splitlines():
        line = line.rstrip()
        if not line.strip() or line.strip() == "\\":
            continue
        lines.append(line)
    while lines and lines[-1].rstrip().endswith("\\"):
        lines[-1] = lines[-1].rstrip()[:-1].rstrip()
        if not lines[-1].strip():
            lines.pop()
    if not lines:
        return None, matches
    return Statement("\n".join(lines), statement.start_line, statement.end_line), matches


def _scope_spectre(text: str, top_name: str) -> ScopeResult:
    statements = _logical_statements(text, "spectre")
    definitions, outside = _subckt_parts(statements, "spectre")
    matches = [item for item in definitions if item.name == top_name]
    if len(matches) != 1:
        raise ScopeError(f"expected exactly one Spectre top subckt {top_name!r}, found {len(matches)}")
    top = matches[0]
    dependencies = _dependencies(top, definitions, "spectre")
    dropped: list[str] = []
    warnings: list[str] = []
    includes: list[Statement] = []
    options: list[Statement] = []
    header: list[Statement] = []
    top_body: list[Statement] = []

    def classify(statement: Statement, *, in_top: bool) -> None:
        lowered = statement.stripped.casefold()
        if not statement.stripped:
            return
        if statement.is_comment("spectre"):
            if in_top:
                top_body.append(statement)
            else:
                header.append(statement)
            return
        if lowered.startswith(("include ", "ahdl_include ", "simulatoroptions ", "scopedoptions ")):
            if lowered.startswith(("simulatoroptions ", "scopedoptions ")):
                filtered, runtime_names = _filter_spectre_runtime_options(statement)
                if runtime_names:
                    dropped.extend(
                        f"line {statement.start_line}: runtime simulator option {name}"
                        for name in runtime_names
                    )
                if filtered is not None:
                    options.append(filtered)
            elif _is_spectre_runtime_include(statement):
                dropped.append(
                    f"line {statement.start_line}: runtime include {statement.stripped.splitlines()[0]}"
                )
            else:
                includes.append(statement)
        elif lowered.startswith("global "):
            # Global nodes cannot be safely made local; preserve only as a
            # diagnostic until a simulator-specific PoC freezes policy.
            warnings.append(f"global statement retained outside top at line {statement.start_line}")
        elif _is_runtime(statement, "spectre"):
            dropped.append(f"line {statement.start_line}: {statement.stripped.splitlines()[0]}")
        elif in_top:
            top_body.append(statement)
        else:
            header.append(statement)
        if not _known_statement(statement, "spectre"):
            warnings.append(
                f"unknown Spectre statement at line {statement.start_line}: "
                f"{statement.stripped.splitlines()[0]}"
            )

    for statement in outside:
        classify(statement, in_top=False)
    for statement in top.body:
        classify(statement, in_top=True)
    # Drop source-generated subcircuit annotation comments from the header;
    # they are attached to the corresponding dependency/top in the golden
    # Cadence output and keeping them globally would change their scope.
    header = [item for item in header if not item.stripped.casefold().startswith("// library name:") and not item.stripped.casefold().startswith("// cell name:") and not item.stripped.casefold().startswith("// view name:") and not item.stripped.casefold().startswith("// end of subcircuit")]
    lines: list[str] = []
    # ``cdsTextTo5x -LANG spectre`` starts in its default spice grammar
    # unless the language directive is present in the imported text.  The
    # raw OCEAN deck normally carries this line, but it can be absent in
    # hand-produced/golden inputs; make the scoped artifact self-describing
    # without duplicating an existing directive.
    if not any(item.stripped.casefold().startswith("simulator lang=") for item in header):
        lines.append("simulator lang=spectre")
    lines.extend(item.text for item in header)
    lines.append(top.header.text)
    lines.extend(item.text for item in options)
    lines.extend(item.text for item in includes)
    for dependency in dependencies:
        # Preserve the nearest source annotation when present in the raw
        # stream.  The structural transform itself never relies on comments.
        lines.append(dependency.header.text)
        lines.extend(item.text for item in dependency.body)
        lines.append(dependency.end.text)
    lines.extend(item.text for item in top_body)
    lines.append(top.end.text)
    output = "\n".join(lines).rstrip() + "\n"
    return ScopeResult("spectre", top_name, _ports(top.header, "spectre"), output, tuple(item.name for item in dependencies), tuple(dropped), tuple(warnings), hashlib.sha256(text.encode()).hexdigest(), hashlib.sha256(output.encode()).hexdigest())


def _scope_hspice(text: str, top_name: str) -> ScopeResult:
    statements = _logical_statements(text, "hspiceD")
    definitions, outside = _subckt_parts(statements, "hspiceD")
    matches = [item for item in definitions if _name_key(item.name, "hspiceD") == _name_key(top_name, "hspiceD")]
    if len(matches) != 1:
        raise ScopeError(f"expected exactly one HSPICE top subckt {top_name!r}, found {len(matches)}")
    top = matches[0]
    dependencies = _dependencies(top, definitions, "hspiceD")
    dropped: list[str] = []
    warnings: list[str] = []
    header: list[Statement] = []
    local: list[Statement] = []
    top_body: list[Statement] = []

    def classify(statement: Statement, *, in_top: bool) -> None:
        lowered = statement.stripped.casefold()
        if not statement.stripped:
            return
        if statement.is_comment("hspiceD"):
            if in_top:
                top_body.append(statement)
            else:
                header.append(statement)
        elif lowered.startswith((".temp", ".option", ".lib", ".include")):
            local.append(statement)
        elif lowered == ".end":
            # Terminal marker is regenerated exactly once below.
            dropped.append(f"line {statement.start_line}: .END")
        elif _is_runtime(statement, "hspiceD"):
            dropped.append(f"line {statement.start_line}: {statement.stripped.splitlines()[0]}")
        elif in_top:
            top_body.append(statement)
        else:
            header.append(statement)
        if not _known_statement(statement, "hspiceD"):
            # HSPICE comments and blank statements are known by definition;
            # only opaque non-comment lines reach this branch.
            warnings.append(
                f"unknown HSPICE statement at line {statement.start_line}: "
                f"{statement.stripped.splitlines()[0]}"
            )

    for statement in outside:
        classify(statement, in_top=False)
    for statement in top.body:
        classify(statement, in_top=True)
    has_parhier = any("parhier" in item.stripped.casefold() for item in local)
    if not has_parhier:
        local.append(Statement(".OPTION PARHIER=LOCAL", 0, 0))
    lines: list[str] = []
    lines.extend(item.text for item in header)
    lines.append(top.header.text)
    lines.extend(item.text for item in local)
    for dependency in dependencies:
        lines.append(dependency.header.text)
        lines.extend(item.text for item in dependency.body)
        lines.append(dependency.end.text)
    lines.extend(item.text for item in top_body)
    lines.append(top.end.text)
    lines.append(".END")
    output = "\n".join(lines).rstrip() + "\n"
    return ScopeResult("hspiceD", top_name, _ports(top.header, "hspiceD"), output, tuple(item.name for item in dependencies), tuple(dropped), tuple(warnings), hashlib.sha256(text.encode()).hexdigest(), hashlib.sha256(output.encode()).hexdigest())


def scope_netlist(text: str, dialect: str, top_name: str) -> ScopeResult:
    """Transform raw input into one locally scoped MTS block."""

    if dialect == "spectre":
        return _scope_spectre(text, top_name)
    if dialect == "hspiceD":
        return _scope_hspice(text, top_name)
    raise ScopeError(f"unsupported dialect: {dialect!r}")
