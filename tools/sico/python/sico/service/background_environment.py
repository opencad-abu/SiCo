"""Materialize worker environments with compatibility exports for bridge snapshots."""

# Compat: retire these exports after supported consumers use the transport contract.
from ..transport.platform_environment import (
    CAPTURED_NAMES, EDA_NAMES, SNAPSHOT_NAMES, capture_environment, validate_environment,
)


def worker_environment(directory, environment=None, *, captured=False):
    env = (validate_environment(environment) if captured
           else capture_environment(environment))
    env.update(PWD=str(directory), CDS_AUTO_64BIT="ALL", PYTHONNOUSERSITE="1",
               PYTHONDONTWRITEBYTECODE="1")
    for key in ("TMPDIR", "TMP", "TEMP", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
        env[key] = str(directory / "scratch")
    return env


def guardian_environment(directory, environment):
    """A Python guardian keeps its selected interpreter, outside captured EDA state."""
    from ..installation import python_path, current
    from ..interpreter import cad_python

    env = worker_environment(directory, environment, captured=True)
    env.update(SICO_PYTHON=cad_python(), SICO_HOME=str(current().root),
               PYTHONPATH=python_path())
    return env
