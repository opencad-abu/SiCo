"""Capture and validate the finite EDA environment sent over an instance bridge."""

import os

from cadenv import VIRTUOSO_ENV_NAMES, captured_virtuoso_environment, restore_virtuoso_environment

from .framing import ProtocolError

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
    restored = restore_virtuoso_environment(source, credential=credential)
    result = {key: value for key, value in restored.items() if key in EDA_NAMES}
    result.update(captured_virtuoso_environment(source, credential=credential))
    for key in tuple(result):
        if credential in (key, "SICO_VIRTUOSO_" + key, "CAD_VIRTUOSO_" + key,
                          "SICO_VIRTUOSO_" + key + "_SET", "CAD_VIRTUOSO_" + key + "_SET"):
            del result[key]
    return validate_environment(result)
