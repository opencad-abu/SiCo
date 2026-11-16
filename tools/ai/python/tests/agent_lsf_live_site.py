"""Explicit site inputs for opt-in AS-06D checks; never builds a runtime or context."""

import hashlib
import math
import os
import pwd
from dataclasses import asdict, dataclass
from pathlib import Path

from sico.service.background_capability import build_background_probe
from sico.service.background_config import load_settings
from sico.service.background_environment import validate_environment
from sico.storage.history import owned_directory
from sico.storage.project_files import read_record
from sico.transport.framing import ProtocolError

CONTRACT = 'cadai.as06.live.v1'


@dataclass(frozen=True)
class LiveSite:
    shared_tests: Path
    alias_tests: object
    target: dict
    environment: dict
    execution: dict
    wait_seconds: float
    max_slots: int
    max_wall_minutes: int


def _path(value):
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ProtocolError('Live acceptance requires explicit absolute paths')
    return Path(value)


def load_site(path):
    """Read and validate only; no directory creation, scheduler calls or EDA startup."""
    row = read_record(_path(str(path)))
    fields = {'protocol', 'execution_config', 'shared_tests', 'alias_tests', 'target',
              'environment', 'wait_seconds', 'max_slots', 'max_wall_minutes'}
    if not isinstance(row, dict) or set(row) != fields or row['protocol'] != CONTRACT:
        raise ProtocolError('Invalid AS-06 live site schema')
    target = row['target']
    if not isinstance(target, dict) or set(target) != {'lib', 'cell', 'view'}:
        raise ProtocolError('Live acceptance requires one explicit lib/cell/view')
    build_background_probe('inspect_schematic', target)
    environment = validate_environment(row['environment'])
    for key in ('max_slots', 'max_wall_minutes'):
        if type(row[key]) is not int or row[key] < 1:
            raise ProtocolError('Invalid approved site resource limit')
    wait = row['wait_seconds']
    if type(wait) not in (int, float) or not math.isfinite(wait) or not 1 <= wait <= 3600:
        raise ProtocolError('Invalid live acceptance wait budget')
    runtime, lsf = load_settings(_path(row['execution_config']))
    if runtime is None or lsf is None:
        raise ProtocolError('Live acceptance requires an LSF execution configuration')
    if lsf.owner != pwd.getpwuid(os.getuid()).pw_name:
        raise ProtocolError('Site configuration belongs to another scheduler owner')
    if lsf.slots > row['max_slots'] or lsf.wall_minutes > row['max_wall_minutes']:
        raise ProtocolError('Execution exceeds approved site resources')
    shared = _path(row['shared_tests'])
    owned_directory(shared)
    lsf.shared(shared)
    alias = None if row['alias_tests'] is None else _path(row['alias_tests'])
    if alias is not None and not os.path.samefile(shared, alias):
        raise ProtocolError('Site aliases do not identify the same shared test directory')
    if target['lib'] not in runtime.libraries:
        raise ProtocolError('Read-only target is outside configured libraries')
    for location in runtime.libraries.values():
        location = _path(str(location))
        lsf.shared(location)
        if not location.is_dir():
            raise ProtocolError('Configured library is not visible on the submit host')
    for executable in (runtime.virtuoso, runtime.xvfb, Path(lsf.node_command[0]),
                       *(Path(getattr(lsf, key)) for key in
                         ('bsub', 'bjobs', 'bkill', 'bhist', 'lsid'))):
        if (not executable.is_absolute() or not executable.is_file()
                or not os.access(executable, os.X_OK)):
            raise ProtocolError('Configured executable is not available on the submit host')
    context = _path(str(runtime.context))
    with context.open('rb') as stream:
        data = stream.read(32 * 1024 * 1024 + 1)
    if (context.suffix != '.cxt' or len(data) > 32 * 1024 * 1024
            or hashlib.sha256(data).hexdigest() != runtime.context_sha256):
        raise ProtocolError('Existing worker context checksum mismatch')
    if not isinstance(runtime.version, str) or not runtime.version.strip():
        raise ProtocolError('Expected worker version is required')
    return LiveSite(shared, alias, dict(target), environment,
                    dict(runtime=asdict(runtime), lsf=asdict(lsf)), wait,
                    row['max_slots'], row['max_wall_minutes'])
