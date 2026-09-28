"""Persist only the finite non-secret inputs needed for explicit session recovery."""

import errno
import hashlib
import json
from cadenv import VIRTUOSO_MARKERS, captured_virtuoso_environment
from sicoenv import RETIRED_NAMES, promote

from ..core.contracts import BoundContext, identifier
from ..providers.config import validate_config
from ..transport.framing import ProtocolError
from .history import owned_directory
from .project_files import RegistryError, read_record, write_record

CONTRACT = "cadai.session.snapshot.v1"
NAME = "session.snapshot.json"
# Deliberate allowlist: arbitrary launch environment is never a persistent snapshot.
ENVIRONMENT = frozenset({
    "PATH", "HOME", "LD_LIBRARY_PATH", "PYTHONPATH", "CAD_PYTHON", "CAD_AI_CONTEXT",
    "SICO_PYTHON", "SICO_AI_CONTEXT",
    "MODULEPATH", "LOADEDMODULES", "_LMFILES_", "MODULESHOME", "LMOD_CMD",
    *VIRTUOSO_MARKERS,
    "SICO_MCP_TIMEOUT", "SICO_MODEL_IDLE_TIMEOUT", "NO_PROXY", "no_proxy",
    "CDS_LIC_FILE", "LM_LICENSE_FILE", "CDS_AUTO_64BIT", "OA_HOME",
})


def fingerprint(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def capture(session_id, context, config, environment, descriptor, project_id=None,
            *, background=None):
    credential = config.get("api_key_env", "SICO_API_KEY") if config else None
    saved = {key: value for key, value in environment.items()
             if key in ENVIRONMENT and key not in VIRTUOSO_MARKERS and key != credential}
    for current in ("SICO_PYTHON", "SICO_AI_CONTEXT"):
        if credential in (current, *RETIRED_NAMES[current]):
            for name in (current, *RETIRED_NAMES[current]):
                saved.pop(name, None)
        else:
            promote(saved, current)
    # New snapshots have one provenance family; historical validation accepts both.
    saved.update({"SICO_VIRTUOSO_" + key: value for key, value in
                  captured_virtuoso_environment(environment, credential=credential).items()})
    # In-memory credentials and bridge tokens are deliberately absent.
    source = {key: descriptor[key] for key in ("bridge_id", "router_id") if key in descriptor}
    value = dict(contract=CONTRACT, session_id=session_id, project_id=project_id,
                 context=context.record(),
                 provider_config=config, environment=saved, bridge_identity=source,
                 background=background)
    return dict(value, revision=fingerprint(value))


def validate(row, session_id):
    try:
        fields = {"contract", "session_id", "project_id", "context", "provider_config",
                  "environment", "bridge_identity", "revision"}
        if (not isinstance(row, dict) or set(row) not in (fields, fields | {"background"})
                or row["contract"] != CONTRACT
                or row["session_id"] != identifier(session_id)):
            raise ValueError()
        if row.get('background') is not None:
            from ..service.background_config import runtime_settings
            from ..service.lsf_config import lsf_settings

            background = row['background']
            if not isinstance(background, dict) or set(background) != {'runtime', 'lsf'}:
                raise ValueError()
            runtime_settings(background['runtime'])
            if background['lsf'] is not None:
                lsf_settings(background['lsf'])
        BoundContext.from_record(row["context"])
        if row["project_id"] is not None:
            identifier(row["project_id"])
        if row["provider_config"] is not None:
            validate_config(row["provider_config"])
        environment, bridge = row["environment"], row["bridge_identity"]
        credential = (row["provider_config"] or {}).get("api_key_env", "SICO_API_KEY")
        if (not isinstance(environment, dict) or set(environment) - ENVIRONMENT
                or credential in environment or any(type(v) is not str or "\0" in v
                                                    for v in environment.values())
                or not isinstance(bridge, dict) or set(bridge) - {"bridge_id", "router_id"}):
            raise ValueError()
        for value in bridge.values():
            identifier(value)
        expected = fingerprint({key: value for key, value in row.items() if key != "revision"})
        if row["revision"] != expected:
            raise ValueError()
        return row
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        raise ProtocolError("Invalid captured session configuration") from exc


def save(directory, row):
    # Provider construction validates configuration before the owner calls this.
    # Strict read validation also protects legacy/mocked providers on recovery.
    write_record(directory / NAME, row)


def read(directory):
    for parent in (directory.parent.parent, directory.parent, directory):
        owned_directory(parent)
    try:
        row = read_record(directory / NAME)
    except FileNotFoundError:
        return None
    except RegistryError as exc:
        raise ProtocolError("Invalid captured configuration storage") from exc
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ProtocolError("Invalid captured configuration path") from exc
        raise
    return validate(row, directory.name)
