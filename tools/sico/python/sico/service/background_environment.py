"""Capture finite EDA values once; materialize them without reinterpreting provenance."""

import os
from cadenv import VIRTUOSO_ENV_NAMES, captured_virtuoso_environment

from ..transport.framing import ProtocolError

EDA_NAMES = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TZ",
    "CDS_LIC_FILE", "LM_LICENSE_FILE", "CDS_AUTO_64BIT", "OA_HOME",
    "MODULEPATH", "LOADEDMODULES", "_LMFILES_", "MODULESHOME", "LMOD_CMD",
})
CAPTURED_NAMES = VIRTUOSO_ENV_NAMES
SNAPSHOT_NAMES = EDA_NAMES | set(CAPTURED_NAMES)


def validate_environment(environment):
    if (not isinstance(environment, dict) or set(environment) - SNAPSHOT_NAMES
            or any(not isinstance(v, str) or "\0" in v or len(v) > 8192
                   for v in environment.values())):
        raise ProtocolError("Invalid captured EDA environment")
    return dict(environment)


def capture_environment(environment=None, *, credential=None):
    source = os.environ if environment is None else environment
    result = {key: value for key, value in source.items() if key in EDA_NAMES}
    result.update(captured_virtuoso_environment(source, credential=credential))
    for key in tuple(result):
        if credential in (key, "SICO_VIRTUOSO_" + key, "CAD_VIRTUOSO_" + key):
            del result[key]
    return validate_environment(result)


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
