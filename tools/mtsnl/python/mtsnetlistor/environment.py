"""Explicit source/target environment and session boundary descriptors."""

from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import json
import os
from pathlib import Path
from typing import Mapping, Optional

from .artifacts import atomic_write_text, sha256_file
from .errors import IsolationError, RequestValidationError


def detach_mps_environment(environment: dict[str, str]) -> tuple[str, ...]:
    """Remove every Cadence multiprocess-session selector in place.

    Cadence may add selectors beyond the currently observed SESSION/HOST/PORT
    set.  Treat the complete ``CDS_MPS_*`` namespace as owned by the host
    Virtuoso session so neither this application nor any worker can join it.
    The removed names are returned for diagnostics and focused tests; values
    are deliberately not exposed because they are session-internal data.
    """

    removed = tuple(sorted(name for name in environment if name.startswith("CDS_MPS_")))
    for name in removed:
        environment.pop(name, None)
    return removed


def _real_file(path: str | Path, label: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise RequestValidationError(f"cannot access {label}: {resolved}")
    return resolved


def _process_start_time(pid: int, *, proc_root: str | Path = "/proc") -> str | None:
    """Return Linux process start-time ticks for ``pid``.

    ``/proc/<pid>/stat`` contains a parenthesized command name, so splitting
    from the final ``)`` avoids being confused by spaces or parentheses in
    that name.  The value at index 19 after the closing parenthesis is field
    22 (``starttime``).  A missing or malformed proc entry is treated as an
    exited process rather than guessed around.
    """

    try:
        text = (Path(proc_root) / str(pid) / "stat").read_text(encoding="ascii")
    except (OSError, UnicodeError):
        return None
    _prefix, separator, fields_text = text.rpartition(")")
    if not separator:
        return None
    fields = fields_text.strip().split()
    if len(fields) <= 19:
        return None
    value = fields[19]
    return value if value.isascii() and value.isdigit() else None


def write_cds_lib_overlay(
    base_cds_lib: str | Path,
    destination: str | Path,
    *,
    define: Optional[tuple[str, str | Path]] = None,
    forbidden_paths: tuple[str | Path, ...] = (),
) -> Path:
    """Create a task-private cds.lib that includes exactly one domain.

    Cadence processes receive this file through ``-cdslib``.  Keeping the
    include and optional transfer-library definition in a run directory makes
    the process boundary auditable and prevents a caller from accidentally
    combining source and target library maps.  The helper deliberately emits
    only absolute paths and a small, fixed grammar; user text never becomes
    executable SKILL or shell input.
    """

    base = _real_file(base_cds_lib, "cds.lib")
    output = Path(destination).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    forbidden = tuple(Path(item).expanduser().resolve() for item in forbidden_paths)
    for path in forbidden:
        if base == path:
            raise IsolationError(f"overlay base cds.lib crosses forbidden domain: {base}")
    # A domain can be reintroduced through an INCLUDE or a literal DEFINE in
    # the base file.  Reject explicit absolute references before handing the
    # overlay to Cadence; this is deliberately conservative and errs toward a
    # visible preflight failure instead of silent cross-domain loading.
    base_text = base.read_text(encoding="utf-8", errors="replace")
    for path in forbidden:
        if str(path) in base_text:
            raise IsolationError(
                f"overlay base cds.lib references forbidden domain path: {path}"
            )
    if define is not None:
        name, raw_path = define
        if not name or not name.replace("_", "a").isalnum() or not (
            name[0].isalpha() or name[0] == "_"
        ):
            raise RequestValidationError(f"invalid overlay library name: {name!r}")
        physical = Path(raw_path).expanduser().resolve()
        if any(physical == path for path in forbidden):
            raise IsolationError(f"overlay definition crosses forbidden domain: {physical}")
        physical.mkdir(parents=True, exist_ok=True)
        if not physical.is_dir():
            raise RequestValidationError(f"overlay library path is not a directory: {physical}")
    lines = [f"INCLUDE {base}"]
    if define is not None:
        lines.append(f"DEFINE {define[0]} {Path(define[1]).expanduser().resolve()}")
    # Refreshing a catalog in the same task directory is expected to replace
    # the previous overlay atomically; readers see either complete version.
    atomic_write_text(output, "\n".join(lines) + "\n")
    return output


@dataclass(frozen=True)
class SessionDescriptor:
    """Snapshot produced by the current Proj Virtuoso launcher."""

    owner_pid: int
    owner_start_time: str
    target_cds_lib: Path
    target_cds_lib_digest: str
    target_library_paths: dict[str, str]
    tool_version: str = "1"

    def validate(self) -> "SessionDescriptor":
        if self.owner_pid <= 0:
            raise RequestValidationError("owner_pid must be positive")
        if not str(self.owner_start_time).strip():
            raise RequestValidationError("owner_start_time must not be empty")
        # Production launchers record Linux /proc start ticks.  Keep a
        # non-numeric marker usable for deterministic unit-test descriptors,
        # but never accept a numeric PID marker when the process is gone or
        # has been reused.
        owner_start = str(self.owner_start_time).strip()
        if owner_start.isdigit():
            current_start = _process_start_time(self.owner_pid)
            if not current_start:
                raise IsolationError(
                    f"owner Virtuoso process is no longer alive: pid {self.owner_pid}"
                )
            if current_start != owner_start:
                raise IsolationError(
                    f"owner Virtuoso PID was reused: pid {self.owner_pid}"
                )
        target = _real_file(self.target_cds_lib, "target cds.lib")
        digest = sha256_file(target)
        if digest != self.target_cds_lib_digest:
            raise IsolationError(
                f"target cds.lib changed since session launch: {target}"
            )
        paths: dict[str, str] = {}
        for library, value in self.target_library_paths.items():
            paths[str(library)] = str(Path(value).expanduser().resolve())
        return SessionDescriptor(self.owner_pid, str(self.owner_start_time), target, digest, paths, self.tool_version)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "owner_pid": self.owner_pid,
            "owner_start_time": self.owner_start_time,
            "target_cds_lib": str(self.target_cds_lib),
            "target_cds_lib_digest": self.target_cds_lib_digest,
            "target_library_paths": dict(sorted(self.target_library_paths.items())),
            "tool_version": self.tool_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "SessionDescriptor":
        if int(value.get("schema_version", 0)) != 1:
            raise RequestValidationError("unsupported session descriptor version")
        raw_paths = value.get("target_library_paths")
        if not isinstance(raw_paths, dict):
            raise RequestValidationError("session target_library_paths must be a table")
        return cls(
            int(value.get("owner_pid", 0)),
            str(value.get("owner_start_time", "")),
            Path(str(value.get("target_cds_lib", ""))),
            str(value.get("target_cds_lib_digest", "")),
            {str(key): str(item) for key, item in raw_paths.items()},
            str(value.get("tool_version", "1")),
        )


def write_session(path: str | Path, descriptor: SessionDescriptor) -> Path:
    validated = descriptor.validate()
    return atomic_write_text(path, json.dumps(validated.to_dict(), indent=2, sort_keys=True) + "\n")


def read_session(path: str | Path) -> SessionDescriptor:
    source = _real_file(path, "session descriptor")
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RequestValidationError(f"invalid session descriptor: {source}") from exc
    if not isinstance(value, dict):
        raise RequestValidationError("session descriptor root must be an object")
    return SessionDescriptor.from_dict(value).validate()


def isolated_environment(
    base: Optional[Mapping[str, str]],
    *,
    cds_lib: Path,
    workdir: Path,
    forbidden_cds_lib: Optional[Path] = None,
    forbidden_paths: tuple[str | Path, ...] = (),
    extra: Optional[Mapping[str, str]] = None,
    nocdsinit: bool = True,
) -> dict[str, str]:
    """Build a Cadence child environment and reject cross-domain leakage."""

    environment = dict(os.environ if base is None else base)
    source = cds_lib.resolve()
    forbidden_values = tuple(Path(item).expanduser().resolve() for item in forbidden_paths)
    if forbidden_cds_lib is not None:
        forbidden_values = (*forbidden_values, Path(forbidden_cds_lib).expanduser().resolve())
    if any(source == item for item in forbidden_values):
        raise IsolationError("source and target cds.lib must not share a Cadence process")
    # Do not let a caller's shell/session CDSLIB selector survive by an
    # alternate variable name.  The source values below are the only Cadence
    # library selectors intentionally exposed to the child.
    for name in (
        "CDS_LIB",
        "CDS_CDSLIB",
        "MTS_NETLISTOR_CDSLIB",
        "MTS_NETLISTOR_TARGET_CDSLIB",
        "SICO_TARGET_CDSLIB",
        "CADENCE_TARGET_CDSLIB",
        "MTS_SOURCE_LIB",
        "MTS_SOURCE_CELL",
        "MTS_TARGET_LIB",
        "MTS_TARGET_CELL",
        "MTS_TARGET_ROOT",
        "MTS_TRANSFER_LIB",
        "MTS_TRANSFER_CELL",
        "MTS_TRANSFER_ROOT",
        "MTS_TRANSFER_REPORT",
        "MTS_TARGET_OVERWRITE",
        "MTS_PROCESS_PID",
    ):
        environment.pop(name, None)
    # A process can be a separate OS child yet still join the host Virtuoso's
    # multiprocess SKILL session through inherited CDS_MPS_* selectors.  A
    # source ``dbAccess``/OCEAN worker carrying that session identity can make
    # the host Library Manager follow its ``-cdslib`` file.  Every Cadence
    # worker must therefore be detached from the Proj Virtuoso MPS session.
    for name in (
        "PYTHONHOME",
        "PYTHONPATH",
        "LD_PRELOAD",
        "LD_AUDIT",
    ):
        environment.pop(name, None)
    detach_mps_environment(environment)
    environment["CDS_LIB"] = str(source)
    environment["CDS_CDSLIB"] = str(source)
    environment["MTS_NETLISTOR_CDSLIB"] = str(source)
    environment["MTS_NETLISTOR_WORKDIR"] = str(workdir.resolve())
    if nocdsinit:
        environment["CDS_NOCDSINIT"] = "1"
    if extra:
        for key, value in extra.items():
            child_key = str(key)
            if not child_key or child_key.startswith("CDS_MPS_") or child_key in {
                "CDS_LIB",
                "CDS_CDSLIB",
                "MTS_NETLISTOR_CDSLIB",
                "MTS_NETLISTOR_WORKDIR",
            }:
                raise IsolationError(f"reserved child environment key: {key}")
            environment[child_key] = str(value)
    for key, value in environment.items():
        for forbidden in forbidden_values:
            if str(forbidden) in str(value):
                raise IsolationError(
                    f"forbidden path leaked into child environment key {key}: {forbidden}"
                )
    return environment
