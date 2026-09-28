"""One immutable launch identity for SiCo project state, independent of installation."""

from __future__ import annotations

import os
import stat
from pathlib import Path, PurePosixPath
from typing import Mapping, MutableMapping

from sicoenv import value as environment_value
from sicomigration import admission

ENVIRONMENT = "SICO_TEMP_DIR"
LEGACY_ENVIRONMENT = "CAD_TEMP_DIR"


def absolute(path: str | Path) -> Path:
    """Normalize lexical components without resolving a site's NAS mount alias."""
    return Path(os.path.abspath(Path(path).expanduser()))


def exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def launch_directory(environment, launch=None) -> Path:
    """Keep a valid logical launch spelling, never a stale inherited PWD."""
    directory = absolute(Path.cwd() if launch is None else launch)
    logical = environment.get("PWD", "")
    if launch is None and logical and Path(logical).is_absolute():
        try:
            if os.path.samefile(logical, directory):
                return absolute(logical)
        except OSError:
            pass
    return directory


def state_path(value, *, launch=None, legacy=False) -> Path:
    """Decode a path; only initial configuration may be relative to launch."""
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError("SICO_TEMP_DIR must name a .sico directory")
    path = Path(value).expanduser()
    if not path.is_absolute():
        if launch is None:
            raise ValueError("A bound SiCo state path must be absolute")
        path = absolute(launch) / path
    path = absolute(path)
    if legacy and path.name == "ai" and path.parent.name in {".cad", ".sico"}:
        path = path.parent
    if path.name not in ({".cad", ".sico"} if legacy else {".sico"}):
        raise ValueError("SICO_TEMP_DIR must end in .sico; unset it to use the launch project")
    return path


def selected_root(environment: Mapping[str, str], *, launch=None) -> Path | None:
    # Empty state settings carry no identity. This exception is local to state
    # selection; nonempty retired settings still require explicit migration.
    configured = {name: environment[name] for name in (ENVIRONMENT, LEGACY_ENVIRONMENT)
                  if name in environment and environment[name] != ""}
    selected = environment_value(configured, ENVIRONMENT, (LEGACY_ENVIRONMENT,))
    if selected is None:
        return None
    directory = launch_directory(environment, launch)
    return state_path(selected, launch=directory)


def root(launch: str | Path | None = None, *, environment=None) -> Path:
    """An explicit launch and an inherited root must identify the same directory.

    When no launch is supplied, the inherited identity survives later chdir calls.
    Callers supplying a design/output directory must not pass it as the launch.
    """
    environment = os.environ if environment is None else environment
    selected = selected_root(environment, launch=launch)
    if launch is None and selected is not None:
        return selected
    directory = launch_directory(environment, launch)
    if not directory.is_dir():
        raise ValueError(f"SiCo launch directory is unavailable: {directory}")
    expected = directory / ".sico"
    if selected is not None and selected != expected:
        try:
            same = os.path.samefile(selected.parent, directory)
        except OSError:
            same = False
        if not same:
            raise ValueError("SICO_TEMP_DIR conflicts with the explicit launch directory")
        return selected
    return expected


def validate_directory(path: Path) -> None:
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077):
        raise ValueError(f"Expected a private owned SiCo state directory: {path}; "
                         "use your own project work directory (mode 0700)")


def project_root(launch: str | Path, *, create=False) -> Path:
    """Select the single state root owned by a project launch directory."""
    launch = absolute(launch)
    if not launch.is_dir():
        raise ValueError(f"SiCo state project directory is unavailable: {launch}; "
                         "check SICO_TEMP_DIR or unset it to use the launch project")
    sico, legacy = launch / ".sico", launch / ".cad"
    has_sico = exists(sico)
    has_legacy = exists(legacy)
    if has_sico:
        validate_directory(sico)
        admitted = admission(sico)
        if has_legacy and admitted is None:
            raise ValueError(".cad and .sico coexist without completed activation")
        return sico
    if has_legacy:
        info = legacy.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("Invalid .cad state root")
        if create:
            raise ValueError("Legacy .cad state is read-only; run sico migrate before writing")
        return legacy
    if not create:
        return sico
    sico.mkdir(mode=0o700, exist_ok=True)
    validate_directory(sico)
    return sico


def project_directory(launch: str | Path, relative: str, *, create=False) -> Path:
    """Select a state subdirectory; only writers create private owned components."""
    suffix = PurePosixPath(relative)
    if not relative or suffix.is_absolute() or ".." in suffix.parts or "\\" in relative:
        raise ValueError("Expected a relative project state directory")
    path = project_root(launch, create=create)
    for name in suffix.parts:
        path = path / name
        if create:
            path.mkdir(mode=0o700, exist_ok=True)
            validate_directory(path)
    return path


def ensure(path: str | Path) -> Path:
    """Create only a selected root; an existing legacy root requires migration first."""
    path = absolute(path)
    if path.name != ".sico" or not path.parent.is_dir():
        raise ValueError("Expected an available launch directory and .sico root")
    try:
        (path.parent / ".cad").lstat()
    except FileNotFoundError:
        pass
    else:
        admission(path)
        return path
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    validate_directory(path)
    admission(path)
    return path


def publish(environment: MutableMapping[str, str], launch: str | Path | None = None) -> Path:
    """Freeze the selected identity without changing vendor temporary variables."""
    selected = root(launch, environment=environment)
    environment[ENVIRONMENT] = str(selected)
    environment.pop(LEGACY_ENVIRONMENT, None)
    return selected


def export_environment(
    environment: MutableMapping[str, str], state: str | Path
) -> Path:
    """Publish only an activated .sico root; legacy state is read-only."""
    selected = absolute(state)
    if selected.name != ".sico" or project_root(selected.parent).name != ".sico":
        raise ValueError("Legacy state is read-only; run sico migrate before writing")
    environment[ENVIRONMENT] = str(selected)
    environment.pop(LEGACY_ENVIRONMENT, None)
    return selected
