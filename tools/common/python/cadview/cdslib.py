"""Resolve existing Cadence libraries from cds.lib files."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from types import MappingProxyType
from pathlib import Path
from typing import Dict, List, Mapping, MutableMapping, Optional, Tuple

from cadenv import detach_cadence_mps_environment

from .errors import Nl2ViewError


_ENVIRONMENT_VARIABLE = re.compile(
    r"\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}|"
    r"(?P<plain>[A-Za-z_][A-Za-z0-9_]*))"
)
_INSTALL_ROOT_EXPRESSION = re.compile(r"\$\([^)]*\)")
_CLA_LIBRARY_ROW = re.compile(r"^\s*\d+\s+(\S+)\s+(.+?)\s*$")


@dataclass(frozen=True)
class LibraryDefinitions:
    """Resolved library paths and COMBINE metadata from a cds.lib graph.

    The parser keeps its recursive stack and mutable accumulators private.
    Consumers receive a stable result that can be passed between providers
    without knowing how includes or assignment statements were evaluated.
    """

    libraries: Mapping[str, Path]
    combine_groups: Mapping[str, Tuple[str, ...]]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "libraries", MappingProxyType(dict(self.libraries))
        )
        object.__setattr__(
            self,
            "combine_groups",
            MappingProxyType(
                {
                    name: tuple(members)
                    for name, members in self.combine_groups.items()
                }
            ),
        )


def _statement_fields(raw_line: str) -> List[str]:
    fields = raw_line.split()
    for index, field in enumerate(fields):
        if field.startswith("#") or field.startswith("--"):
            return fields[:index]
    return fields


def _expand_path(
    value: str,
    base_directory: Path,
    environ: Mapping[str, str],
    source_file: Path,
) -> Path:
    expression = _INSTALL_ROOT_EXPRESSION.search(value)
    if expression:
        raise Nl2ViewError(
            "Cadence installation-root expression requires cdsLibDebug: "
            f"{expression.group(0)} in {source_file}"
        )
    missing: List[str] = []

    def replace_variable(match: re.Match[str]) -> str:
        name = match.group("braced") or match.group("plain")
        if name not in environ:
            missing.append(name)
            return match.group(0)
        return environ[name]

    expanded = _ENVIRONMENT_VARIABLE.sub(replace_variable, value)
    if missing:
        names = ", ".join(f"${name}" for name in sorted(set(missing)))
        raise Nl2ViewError(
            f"undefined environment variable {names} in {source_file}"
        )
    if expanded == "~" or expanded.startswith("~/"):
        home = environ.get("HOME")
        if not home:
            raise Nl2ViewError(f"HOME is not set while reading {source_file}")
        expanded = home + expanded[1:]
    elif expanded.startswith("~"):
        expanded = os.path.expanduser(expanded)
        if expanded.startswith("~"):
            raise Nl2ViewError(
                f"cannot expand user home in {value!r} from {source_file}"
            )
    path = Path(expanded)
    if not path.is_absolute():
        path = base_directory / path
    return path.resolve()


def _read_definitions(
    cds_library_file: Path,
    definitions: MutableMapping[str, Path],
    environ: Mapping[str, str],
    stack: Tuple[Path, ...],
    combine_groups: MutableMapping[str, Tuple[str, ...]] | None = None,
) -> None:
    path = cds_library_file.resolve()
    if path in stack:
        chain = " -> ".join(str(item) for item in (*stack, path))
        raise Nl2ViewError(f"recursive cds.lib INCLUDE: {chain}")
    if not path.is_file():
        raise Nl2ViewError(f"cannot access included cds.lib: {path}")

    current_stack = (*stack, path)
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
    ):
        fields = _statement_fields(raw_line)
        if not fields:
            continue
        statement = fields[0].upper()
        if statement in {"DEFINE", "SOFTDEFINE"}:
            if len(fields) != 3:
                raise Nl2ViewError(
                    f"invalid {statement} at {path}:{line_number}"
                )
            try:
                library_path = _expand_path(
                    fields[2], path.parent, environ, path
                )
            except Nl2ViewError:
                if statement == "SOFTDEFINE":
                    continue
                raise
            if library_path.is_dir():
                definitions[fields[1]] = library_path
        elif statement == "UNDEFINE":
            if len(fields) != 2:
                raise Nl2ViewError(f"invalid UNDEFINE at {path}:{line_number}")
            definitions.pop(fields[1], None)
        elif statement in {"INCLUDE", "SOFTINCLUDE"}:
            if len(fields) != 2:
                raise Nl2ViewError(
                    f"invalid {statement} at {path}:{line_number}"
                )
            try:
                included = _expand_path(fields[1], path.parent, environ, path)
            except Nl2ViewError:
                if statement == "SOFTINCLUDE":
                    continue
                raise
            if statement == "SOFTINCLUDE" and not included.is_file():
                continue
            _read_definitions(
                included,
                definitions,
                environ,
                current_stack,
                combine_groups,
            )
        elif statement in {"ASSIGN", "AASSIGN"}:
            # ``ASSIGN name COMBINE ...`` is the cds.lib spelling used by
            # Virtuoso Library Manager.  A few site generators historically
            # emitted ``AASSIGN``; accepting that alias is harmless and keeps
            # the parser useful for those files.  Other ASSIGN attributes
            # (TMP, DISPLAY, etc.) do not affect library enumeration.
            if len(fields) >= 4 and fields[2].upper() == "COMBINE":
                # Cadence ignores COMBINE when the virtual root has not yet
                # been DEFINEd.  Member libraries, however, may be defined by
                # a later statement and their names must remain intact.
                if combine_groups is not None and fields[1] in definitions:
                    combine_groups[fields[1]] = tuple(fields[3:])
        elif statement == "UNASSIGN":
            # A later cds.lib can explicitly remove an inherited COMBINE
            # assignment.  Mirror Cadence's last-statement-wins semantics so
            # the filesystem preview does not retain a stale virtual root.
            if (
                len(fields) >= 3
                and fields[2].upper() == "COMBINE"
                and combine_groups is not None
            ):
                combine_groups.pop(fields[1], None)


def read_library_definitions(
    cds_library_file: Path,
    *,
    environ: Optional[Mapping[str, str]] = None,
) -> LibraryDefinitions:
    """Resolve a cds.lib graph into an immutable domain result."""

    definitions: Dict[str, Path] = {}
    combine_groups: Dict[str, Tuple[str, ...]] = {}
    _read_definitions(
        Path(cds_library_file),
        definitions,
        dict(os.environ if environ is None else environ),
        (),
        combine_groups,
    )
    return LibraryDefinitions(definitions, combine_groups)


def _resolve_with_cadence(
    cds_library_file: Path,
    library: str,
    executable: str,
    environ: Mapping[str, str],
) -> Path:
    child_environment = dict(environ)
    detach_cadence_mps_environment(child_environment)
    try:
        completed = subprocess.run(
            [executable, "-cdslib", str(cds_library_file), "-cla"],
            env=child_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            check=False,
        )
    except (FileNotFoundError, PermissionError) as exc:
        raise Nl2ViewError(
            f"cannot execute cdsLibDebug {executable!r}: {exc}"
        ) from exc
    output = "\n".join((completed.stdout, completed.stderr))
    if completed.returncode != 0:
        detail = next(
            (line.strip() for line in output.splitlines() if line.strip()),
            f"exit status {completed.returncode}",
        )
        raise Nl2ViewError(f"cdsLibDebug could not parse {cds_library_file}: {detail}")

    in_definitions = False
    library_path: Optional[Path] = None
    for line in completed.stdout.splitlines():
        if line.strip() == "Libraries defined:":
            in_definitions = True
            continue
        if not in_definitions:
            continue
        match = _CLA_LIBRARY_ROW.match(line)
        if match and match.group(1) == library:
            library_path = Path(match.group(2)).expanduser().resolve()
    if library_path is None:
        detail = next(
            (
                line.strip()
                for line in completed.stderr.splitlines()
                if library in line and line.strip()
            ),
            None,
        )
        suffix = f": {detail}" if detail else ""
        raise Nl2ViewError(
            f"library {library!r} is not resolved by {cds_library_file}{suffix}"
        )
    if not library_path.is_dir():
        raise Nl2ViewError(
            f"library {library!r} directory does not exist: {library_path}"
        )
    return library_path


def resolve_library_path(
    cds_library_file: Path,
    library: str,
    environ: Optional[Mapping[str, str]] = None,
    cadence_executable: Optional[str] = None,
) -> Path:
    """Resolve an existing library without letting cdsTextTo5x create one."""

    environment = dict(os.environ if environ is None else environ)
    # Keep both the pure parser and cdsLibDebug branch detached from any host
    # Virtuoso multiprocess session.
    detach_cadence_mps_environment(environment)
    if cadence_executable:
        return _resolve_with_cadence(
            cds_library_file, library, cadence_executable, environment
        )
    definitions = read_library_definitions(
        cds_library_file,
        environ=environment,
    )
    library_path = definitions.libraries.get(library)
    if library_path is None:
        raise Nl2ViewError(
            f"library {library!r} is not defined by {cds_library_file}"
        )
    if not library_path.is_dir():
        raise Nl2ViewError(
            f"library {library!r} directory does not exist: {library_path}"
        )
    return library_path
