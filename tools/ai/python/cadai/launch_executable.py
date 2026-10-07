"""Resolve and qualify executables used by managed AI sessions."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from sicoenv import read as environment_setting
from .installation import current, runtime_root

from .codex_selection import (
    CodexConfigurationError,
    require_supported_runtime,
    verify_codex_version,
)

_AGENT_CLI_ENV = {
    "codex": "SICO_CODEX_CLI",
    "claude": "SICO_CLAUDE_CLI",
}


_CODEX_RUNTIME_RELATIVE = Path(
    "runtime/codex/x86_64-unknown-linux-musl/bin/codex"
)


_EXPECTED_PYTHON_VERSION = (3, 9, 13)


_DEFAULT_PYTHON_ROOT = Path("/software/pkgs/python/3.9.13")


def production_python(
    environment: Mapping[str, str] | None = None,
    *,
    require_environment: bool = False,
) -> str:
    """Resolve the production interpreter for controller child processes.

    An explicit ``SICO_PYTHON`` takes precedence. ``SICO_PYTHON_ROOT/bin/python3``
    is only a fallback when ``SICO_PYTHON`` is unset, so a stale site-wide root
    cannot override a project-selected interpreter. For embedded callers and
    source-tree tests, an entirely unset environment uses the qualified default.
    No PATH lookup or ``sys.executable`` fallback is permitted.
    """

    source = os.environ if environment is None else environment
    root_value = environment_setting(source, "SICO_PYTHON_ROOT", "").strip()
    python_value = environment_setting(source, "SICO_PYTHON", "").strip()
    for name, configured in (("SICO_PYTHON", python_value), ("SICO_PYTHON_ROOT", root_value)):
        if name in source and not configured:
            raise RuntimeError(name + " must not be empty")
    if not python_value and not root_value and require_environment:
        raise RuntimeError("SICO_PYTHON or SICO_PYTHON_ROOT must be set")
    root = Path(root_value).expanduser() if root_value else _DEFAULT_PYTHON_ROOT
    executable = Path(python_value).expanduser() if python_value else root / "bin" / "python3"
    if not executable.is_absolute():
        raise RuntimeError("selected production Python must be an absolute path")
    try:
        executable_real = executable.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise RuntimeError("configured production Python cannot be resolved") from exc
    if not executable_real.is_file() or not os.access(executable_real, os.X_OK):
        raise RuntimeError("configured production Python is unavailable")
    probe_environment = {
        key: value
        for key, value in source.items()
        if key not in {"PYTHONHOME", "PYTHONPATH", "LD_PRELOAD", "LD_AUDIT"}
    }
    probe_environment["PYTHONNOUSERSITE"] = "1"
    probe_environment["PYTHONDONTWRITEBYTECODE"] = "1"
    # When called from the fixed interpreter itself, the canonical executable
    # and version have already been established by the launcher.  Avoid a
    # nested subprocess probe here; this also keeps controller unit tests that
    # replace ``Popen`` focused on the terminal child they are exercising.
    if os.path.realpath(sys.executable) == str(executable_real):
        return python_value or str(executable)
    try:
        probe = subprocess.run(
            [
                str(executable),
                "-s",
                "-c",
                "import sys; print('%d.%d.%d' % sys.version_info[:3])",
            ],
            env=probe_environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("configured production Python failed its version probe") from exc
    expected = ".".join(str(item) for item in _EXPECTED_PYTHON_VERSION)
    if probe.returncode != 0 or probe.stdout.strip() != expected:
        raise RuntimeError("production Python must be exactly 3.9.13")
    return python_value or str(executable)


def resolve_executable(explicit: str | None, names: Sequence[Path | str]) -> str:
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    candidates.extend(str(item) for item in names)
    for candidate in candidates:
        if os.path.sep in candidate:
            path = Path(candidate).expanduser()
            if path.is_file() and os.access(path, os.X_OK):
                return str(path.resolve())
        elif resolved := shutil.which(candidate):
            return resolved
    raise FileNotFoundError(f"cannot find executable from: {candidates}")


def _launcher_uses_env_node(executable: str) -> bool:
    """Return whether *executable* has the npm-style ``env node`` shebang."""
    try:
        with Path(executable).open("rb") as stream:
            first_line = stream.readline(512)
    except OSError:
        return False
    if not first_line.startswith(b"#!"):
        return False
    try:
        words = shlex.split(first_line[2:].decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, ValueError):
        return False
    return (
        len(words) >= 2
        and Path(words[0]).name == "env"
        and words[1] == "node"
    )


def prepare_agent_runtime_environment(
    agent: str, executable: str, environment: dict[str, str]
) -> None:
    """Make an agent launcher's interpreter available in its final environment.

    The npm Codex entry point is executable itself but uses
    ``#!/usr/bin/env node``. LSF nodes can therefore pass executable resolution
    and still exit with status 127 when a site setup accidentally omits Node.js
    from ``PATH``. Honor either common meaning of ``NODEJS_HOME`` (the
    installation prefix or its ``bin`` directory) before failing with a
    diagnostic that reaches the Virtuoso CIW.
    """
    if agent != "codex" or not _launcher_uses_env_node(executable):
        return

    path = environment.get("PATH", "").strip() or os.defpath
    if shutil.which("node", path=path):
        return

    node_home_value = environment.get("NODEJS_HOME", "").strip()
    if node_home_value:
        node_home = Path(node_home_value).expanduser()
        if node_home.is_absolute():
            for candidate in (node_home / "node", node_home / "bin" / "node"):
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    node_directory = str(candidate.parent)
                    environment["PATH"] = os.pathsep.join((node_directory, path))
                    return

    raise RuntimeError(
        "Codex launcher requires Node.js, but 'node' is not executable in "
        "PATH and NODEJS_HOME does not identify a usable node executable"
    )


def resolve_agent_executable(
    agent: str,
    explicit: str | None = None,
    environment: Mapping[str, str] | None = None,
    *,
    install_root: Path | None = None,
    install_roots: Sequence[Path | str] | None = None,
) -> str:
    try:
        environment_name = _AGENT_CLI_ENV[agent]
    except KeyError as exc:
        raise ValueError(f"unsupported AI agent: {agent}") from exc

    source = os.environ if environment is None else environment
    candidates: list[Path | str] = []
    explicit_value = explicit.strip() if explicit else ""
    configured = environment_setting(source, environment_name, "").strip()
    if environment_name in source and not configured and not explicit_value:
        raise ValueError(environment_name + " must not be empty")
    product = current(source)
    site_wrapper = product.root / "bin" / agent
    if agent == "claude":
        if site_wrapper is not None:
            candidates.append(site_wrapper)
        if explicit_value or configured:
            candidates.append(explicit_value or configured)
        return resolve_executable(None, [*candidates, agent])

    require_supported_runtime(source)
    requested = explicit_value or configured
    roots = [runtime_root(source, (*tuple(install_roots or ()),
                                 *((install_root,) if install_root is not None else ())))]
    if requested:
        candidates.append(requested)
    else:
        candidates.extend(Path(root) / _CODEX_RUNTIME_RELATIVE for root in roots)
        if site_wrapper is not None:
            candidates.append(site_wrapper)
        candidates.append(agent)
    for candidate in candidates:
        name = str(candidate)
        if os.path.sep in name:
            path = Path(name).expanduser()
            executable = (
                str(path.resolve()) if path.is_file() and os.access(path, os.X_OK) else None
            )
        else:
            executable = shutil.which(name, path=source.get("PATH", os.defpath))
        if executable:
            probe_environment = dict(source)
            prepare_agent_runtime_environment(agent, executable, probe_environment)
            verify_codex_version(executable, probe_environment)
            return executable
    raise CodexConfigurationError(
        "Codex executable was not found; check the configured CLI path or installed runtime"
    )
