"""Production project/modulefile discovery for MTS source workers.

The host Virtuoso shell must never be mutated when a user selects another
technology project.  This module therefore evaluates Environment Modules in a
short-lived subprocess and returns a detached environment snapshot.  Only the
project name is persisted in a workspace; expanded variables (which may
contain site credentials) stay in memory and are used at child-process
boundaries only.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import time
from typing import Mapping, MutableMapping, Optional

from .errors import RequestValidationError
from .moduleenv import (
    MODULECMD_ENV,
    apply_python_modulecmd,
    find_modulecmd,
    neutral_environment,
    prepend_cadence_paths,
)


MODULE_ROOT_ENV = "MTS_NETLISTOR_MODULEFILES"


def module_roots(
    root: str | Path | None = None,
    *,
    environment: Optional[Mapping[str, str]] = None,
) -> tuple[Path, ...]:
    """Resolve the explicitly configured project module directory."""

    env = os.environ if environment is None else environment
    raw = str(root or env.get(MODULE_ROOT_ENV, "")).strip()
    if not raw:
        raise RequestValidationError(
            f"{MODULE_ROOT_ENV} is not configured; set it to the project modulefile directory"
        )
    try:
        resolved = Path(raw).expanduser().resolve()
        if resolved.is_dir():
            return (resolved,)
    except (OSError, RuntimeError) as exc:
        raise RequestValidationError(f"Cannot resolve project module directory: {raw}") from exc
    raise RequestValidationError(f"Project module directory does not exist: {resolved}")


def _project_name(name: str) -> str:
    value = str(name).strip()
    if not value or value in {".", ".."}:
        raise RequestValidationError("project name must not be empty")
    # Project names are relative module names, never arbitrary paths.  A
    # nested name such as ``foundry/65`` is allowed, but traversal is not.
    relative = Path(value)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or any(ord(char) < 32 or char == "\\" for char in value)
        or os.pathsep in value
    ):
        raise RequestValidationError(f"project name must be a module name: {name!r}")
    return value


def _selected_module(roots: tuple[Path, ...], name: str) -> tuple[Path, Path]:
    """Return the first configured root containing *name*."""

    for candidate in roots:
        try:
            return candidate, _module_file(candidate, name)
        except RequestValidationError:
            continue
    rendered = ", ".join(str(item) for item in roots)
    raise RequestValidationError(
        f"project modulefile not found: {name!r} (searched {rendered})"
    )


def _module_file(root: Path, name: str) -> Path:
    value = _project_name(name)
    path = (root / value).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise RequestValidationError(f"project name escapes module root: {name!r}") from exc
    if not path.is_file():
        raise RequestValidationError(f"project modulefile does not exist: {path}")
    return path


def discover_projects(
    root: str | Path | None = None,
    *,
    environment: Optional[Mapping[str, str]] = None,
) -> tuple[str, ...]:
    """List usable modulefile names below *root*.

    Hidden files, editor backups, directories, and ``.version`` metadata are
    excluded.  Only standard Environment Modules files beginning with
    ``#%Module`` are offered.  Names use POSIX separators so they remain
    portable in TOML and can be passed back to ``resolve_project`` without
    ambiguity.
    """

    result: list[str] = []
    for candidate_root in module_roots(root, environment=environment):
        if not candidate_root.is_dir():
            continue
        try:
            entries = sorted(candidate_root.rglob("*"), key=lambda item: item.as_posix())
        except OSError:
            continue
        for path in entries:
            if not path.is_file() or any(part.startswith(".") for part in path.relative_to(candidate_root).parts):
                continue
            relative = path.relative_to(candidate_root).as_posix()
            if relative.endswith(("~", ".bak", ".swp")) or relative.endswith("/.version"):
                continue
            try:
                _project_name(relative)
            except RequestValidationError:
                continue
            try:
                with path.open("r", encoding="utf-8", errors="replace") as stream:
                    header = stream.readline().lstrip()
            except OSError:
                continue
            if not header.startswith("#%Module"):
                continue
            if relative not in result:
                result.append(relative)
    return tuple(result)


def load_project_environment(
    name: str,
    *,
    root: str | Path | None = None,
    modulecmd: str | Path | None = None,
    base_environment: Optional[Mapping[str, str]] = None,
    timeout: float = 20.0,
    timings: Optional[MutableMapping[str, float]] = None,
) -> dict[str, str]:
    """Expand one project modulefile without changing the caller's shell.

    When supplied, ``timings`` receives non-sensitive millisecond durations.
    It never receives module output or environment names/values.
    """

    project_name = _project_name(name)
    base = dict(os.environ if base_environment is None else base_environment)
    roots = module_roots(root, environment=base)
    selected_root, selected_file = _selected_module(roots, project_name)

    command = find_modulecmd(modulecmd, environment=base)
    child = neutral_environment(base)
    # Ensure modulecmd resolves the selected module tree first.  Existing
    # MODULEPATH entries remain available for module.head/module.tail and
    # site-provided dependencies.
    module_path = str(selected_root)
    inherited_module_path = child.get("MODULEPATH", "").strip()
    child["MODULEPATH"] = module_path + ((os.pathsep + inherited_module_path) if inherited_module_path else "")
    try:
        purge_started = time.perf_counter()
        completed = subprocess.run(
            [command, "python", "purge"],
            env=child,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            check=False,
            timeout=timeout,
        )
        if timings is not None:
            timings["modulecmd_purge_ms"] = (
                time.perf_counter() - purge_started
            ) * 1000.0
        if completed.returncode != 0:
            detail = next((line.strip() for line in completed.stderr.splitlines() if line.strip()), "purge failed")
            raise RequestValidationError(f"modulecmd purge failed: {detail}")
        if not apply_python_modulecmd(completed.stdout, child):
            raise RequestValidationError(
                f"modulecmd purge reported failure for project {project_name!r}"
            )
        load_started = time.perf_counter()
        completed = subprocess.run(
            [command, "python", "load", str(selected_file)],
            env=child,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            check=False,
            timeout=timeout,
        )
        if timings is not None:
            timings["modulecmd_load_ms"] = (
                time.perf_counter() - load_started
            ) * 1000.0
    except subprocess.TimeoutExpired as exc:
        raise RequestValidationError(f"loading project module timed out: {project_name}") from exc
    except OSError as exc:
        raise RequestValidationError(f"cannot execute modulecmd: {exc}") from exc
    if completed.returncode != 0:
        detail = next((line.strip() for line in completed.stderr.splitlines() if line.strip()), f"exit status {completed.returncode}")
        raise RequestValidationError(f"loading project module {project_name!r} failed: {detail}")
    assigned_keys: set[str] = set()
    if not apply_python_modulecmd(
        completed.stdout,
        child,
        assigned_keys=assigned_keys,
        require_status=True,
    ):
        raise RequestValidationError(f"modulecmd reported failure loading project {project_name!r}")
    if "PROJ_USER_HOME" not in assigned_keys:
        raise RequestValidationError(
            f"project module {project_name!r} did not define PROJ_USER_HOME"
        )
    prepend_cadence_paths(child)
    return child


@dataclass(frozen=True)
class ProjectContext:
    """Resolved source-project identity and its private environment snapshot."""

    name: str
    module_file: Path
    environment: dict[str, str]
    project_user_home: Path
    cds_lib: Path
    timings: tuple[tuple[str, float], ...] = ()

    @property
    def project_home(self) -> Path | None:
        raw = self.environment.get("PROJ_HOME", "").strip()
        return Path(raw).expanduser().resolve() if raw else None


def resolve_project(
    name: str,
    *,
    root: str | Path | None = None,
    modulecmd: str | Path | None = None,
    base_environment: Optional[Mapping[str, str]] = None,
    require_cds_lib: bool = True,
    timeout: float = 20.0,
) -> ProjectContext:
    """Load a project module and resolve its conventional user cds.lib."""

    resolve_started = time.perf_counter()
    project_name = _project_name(name)
    base = dict(os.environ if base_environment is None else base_environment)
    roots = module_roots(root, environment=base)
    selected_root, module_file = _selected_module(roots, project_name)
    timings: dict[str, float] = {}
    environment = load_project_environment(
        project_name,
        root=selected_root,
        modulecmd=modulecmd,
        base_environment=base,
        timeout=timeout,
        timings=timings,
    )
    raw_home = environment.get("PROJ_USER_HOME", "").strip()
    if not raw_home:
        raise RequestValidationError(
            f"project module {project_name!r} did not define PROJ_USER_HOME"
        )
    project_user_home = Path(raw_home).expanduser().resolve()
    cds_lib = (project_user_home / "cds.lib").resolve()
    if require_cds_lib and not cds_lib.is_file():
        raise RequestValidationError(
            f"project {project_name!r} default cds.lib is unavailable: {cds_lib}"
        )
    # The selected cds.lib is authoritative for these Cadence selectors.
    # A host launched from a legacy setup script may have set CDS_LIB without
    # loading an Environment Modules project, so ``modulecmd purge`` cannot
    # know that it belongs to the host project.  Override it explicitly after
    # the module has loaded to prevent source workers from retaining the host
    # session's library-definition path through a secondary selector.
    environment["CDS_LIB"] = str(cds_lib)
    environment["CDS_CDSLIB"] = str(cds_lib)
    timings["project_resolve_ms"] = (
        time.perf_counter() - resolve_started
    ) * 1000.0
    return ProjectContext(
        project_name,
        module_file,
        environment,
        project_user_home,
        cds_lib,
        tuple(sorted(timings.items())),
    )


__all__ = [
    "MODULECMD_ENV", "MODULE_ROOT_ENV", "ProjectContext",
    "discover_projects", "load_project_environment", "module_roots", "resolve_project",
]
