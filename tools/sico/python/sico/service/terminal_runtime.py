"""Opt-in product composition of the one session terminal over managed exec."""

from pathlib import Path

from ..codex.exec_policy import process_environment
from ..execution.managed_bash import ManagedBashBackend
from ..process_identity import boot_id
from .session_terminal import SessionTerminal
from .terminal_environment import shell_environment

ROUTE_FLAG = "SICO_TERMINAL_ROUTE"
ENVIRONMENT_ID = "session"


def _directory(value, fallback):
    path = Path(value or fallback).resolve()
    if not path.is_dir():
        raise ValueError("Terminal home directory is unavailable")
    return path


def _private(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def make_session_terminal(provider, journal, context, environment, project):
    """Build the opt-in managed terminal, or return None when not selected.

    The product route is selected only by the captured SICO_TERMINAL_ROUTE=1
    flag. It uses a managed workspace-write sandbox with network disabled and
    never falls back to an unsandboxed shell.
    """
    if not isinstance(environment, dict) or environment.get(ROUTE_FLAG) != "1":
        return None
    executable = getattr(provider, "executable", None)
    if not callable(executable):
        raise ValueError("Terminal route requires a provider executable")
    binary = Path(executable()).resolve()
    if not binary.is_file():
        raise ValueError("Terminal provider executable is unavailable")
    cwd = Path(project).resolve()
    if not cwd.is_dir():
        raise ValueError("Terminal project directory is unavailable")
    home = _directory(environment.get("HOME"), cwd)
    control = _private(journal.directory / "terminal-control")
    codex_home = _private(journal.directory / "terminal-control-codex")
    control_environment = process_environment({
        "PATH": environment.get("PATH", "/usr/bin:/bin"),
        "HOME": str(control),
        "CODEX_HOME": str(codex_home),
    })
    child_environment = shell_environment(environment)
    access = dict(sandbox="workspace-write", approval="never", network=False)

    def backend():
        return ManagedBashBackend(
            binary, control_environment, cwd=cwd, home=home,
            environment=child_environment, environment_id=ENVIRONMENT_ID,
            access=access, writable_roots=(cwd,))

    return SessionTerminal(
        journal, context, cwd=cwd, environment_id=ENVIRONMENT_ID,
        backend_factory=backend, host_id="sico-terminal", boot_id=boot_id())
