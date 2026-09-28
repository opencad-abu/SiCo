"""Project scratch environment shared by AI, flow and utility processes."""

from __future__ import annotations

import os

from cadenv import preserve_eda_temp_environment
from sicostate import (export_environment, launch_directory, project_directory,
                       project_root, selected_root, state_path, validate_directory)


def same_root(first, second):
    if first == second:
        return True
    if first.name != second.name:
        return False
    try:
        return os.path.samefile(first.parent, second.parent)
    except OSError:
        return False


def selected_state(environment=None, *, cwd=None, temporary=None, create=False):
    """An inherited launch identity survives chdir; new projects use .sico.

    Historical .cad discovery is read-only; writers require explicit activation.
    """
    source = os.environ if environment is None else environment
    selected = selected_root(source, launch=cwd)
    if temporary is not None:
        candidate = state_path(temporary)
        if selected is not None and not same_root(selected, candidate):
            raise ValueError("temporary root conflicts with the inherited launch identity")
        selected = selected or candidate
    if selected is None:
        return project_root(launch_directory(source, cwd), create=create)
    actual = project_root(selected.parent)
    if not same_root(actual, selected):
        raise ValueError("state root requires explicit migration; refusing a parallel root")
    return project_root(selected.parent, create=create)


def ai_directory(environment=None, *, cwd=None, temporary=None, create=False):
    state = selected_state(environment, cwd=cwd, temporary=temporary, create=create)
    return project_directory(state.parent, "ai", create=create)


def initialize(environment=None, *, cwd=None, temporary=None):
    """Preserve EDA settings before redirecting AI scratch, cache and runtime."""
    target = os.environ if environment is None else environment
    state = selected_state(target, cwd=cwd, temporary=temporary, create=True)
    return _redirect_environment(target, state, "ai")


def initialize_project(environment=None, *, cwd=None, temporary=None):
    """Prepare the project scratch used by independent flows and batch tasks."""
    target = os.environ if environment is None else environment
    state = selected_state(target, cwd=cwd, temporary=temporary, create=True)
    return _redirect_environment(target, state, "")


def _redirect_environment(target, state, relative):
    directory = project_directory(state.parent, relative, create=True) if relative else state
    validate_directory(directory)
    prefix = relative + "/" if relative else ""
    for name in ("cache", "runtime"):
        project_directory(state.parent, prefix + name, create=True)
    export_environment(target, state)
    preserve_eda_temp_environment(target)
    for name in ("TMPDIR", "TMP", "TEMP", "SQLITE_TMPDIR"):
        target[name] = str(directory)
    target["XDG_CACHE_HOME"] = str(directory / "cache")
    target["XDG_RUNTIME_DIR"] = str(directory / "runtime")
    target["PYTHONDONTWRITEBYTECODE"] = "1"
    return directory


def exec_command(arguments):
    """Development shell adapter; native launchers import the same function."""
    import sys

    try:
        initialize()
        os.execv(arguments[0], arguments)
    except (OSError, ValueError) as exc:
        print("SiCo terminal: " + str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
