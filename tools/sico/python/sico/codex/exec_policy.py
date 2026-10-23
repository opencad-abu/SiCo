"""Project the acknowledged Codex access policy into managed exec-server scope."""

from pathlib import Path

from .access_policy import validate


def _directory(value):
    path = Path(value)
    if not path.is_absolute() or not path.is_dir():
        raise ValueError("Execution scope requires existing absolute directories")
    return path.resolve().as_uri()


def sandbox_request(access, cwd, *, home, writable_roots=()):
    """Trusted composition supplies roots; commands cannot replace this scope.

    This first adapter supports managed scopes only. It never substitutes
    process/spawn or full-access execution when a sandbox fails to start.
    """
    access = validate(access)
    if access["sandbox"] == "danger-full-access":
        raise ValueError("Session execution requires a managed sandbox")
    if access["sandbox"] == "read-only" and writable_roots:
        raise ValueError("Read-only execution cannot declare writable roots")
    cwd_uri, home_uri = _directory(cwd), _directory(home)
    entries = [dict(path=dict(type="special", value=dict(kind="root")), access="read")]
    roots = tuple(dict.fromkeys((cwd_uri, *map(_directory, writable_roots))))
    if access["sandbox"] == "workspace-write":
        entries.extend(dict(path=dict(type="path", path=uri), access="write") for uri in roots)
    return dict(permissions=dict(type="managed", network="enabled" if access["network"] else "restricted",
                                 file_system=dict(type="restricted", entries=entries)),
                cwd=cwd_uri, workspaceRoots=list(roots), userHomeDir=home_uri,
                windowsSandboxLevel="disabled")


def process_environment(environment):
    """Detach an explicit child environment without inheriting server credentials."""
    if (not isinstance(environment, dict)
            or any(not isinstance(key, str) or not key or "=" in key or "\0" in key
                   or not isinstance(value, str) or "\0" in value for key, value in environment.items())):
        raise ValueError("Invalid managed process environment")
    return dict(environment)
