"""Load explicit batch execution settings; interactive LSF configuration is separate."""

from pathlib import Path

from ..transport.framing import ProtocolError, strict_json
from .background_worker import BackgroundRuntime, _read
from .lsf_config import lsf_settings


def load_settings(path):
    if not path:
        return None, None
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('Background configuration must be absolute')
    row = strict_json(_read(path))
    if not isinstance(row, dict):
        raise ProtocolError('Invalid background execution configuration')
    protocol = row.pop('protocol', None)
    if protocol == 'cad_ai_background_runtime.v1':
        return runtime_settings(row), None
    if protocol != 'cadai.background.execution.v1' or set(row) != {'runtime', 'lsf'}:
        raise ProtocolError('Invalid background execution configuration')
    return runtime_settings(row['runtime']), lsf_settings(row['lsf'])


def runtime_settings(row):
    required = {'virtuoso', 'context', 'context_sha256', 'version', 'libraries'}
    if not isinstance(row, dict) or not required <= set(row) <= required | {'xvfb'}:
        raise ProtocolError('Invalid background runtime configuration')
    try:
        return BackgroundRuntime(**row)
    except (TypeError, ValueError) as exc:
        raise ProtocolError('Invalid background runtime values') from exc


def capture_settings(environment):
    import json
    from dataclasses import asdict

    runtime, lsf = load_settings(environment.get('SICO_BACKGROUND_CONFIG'))
    if runtime is None:
        return None
    return json.loads(json.dumps(dict(runtime=asdict(runtime),
        lsf=None if lsf is None else asdict(lsf)), default=str))


def captured_backend(root, settings):
    from .local_execution import LocalExecutionBackend
    from .lsf_execution import LsfExecutionBackend

    if settings is None:
        return LocalExecutionBackend(root, None)
    if not isinstance(settings, dict) or set(settings) != {'runtime', 'lsf'}:
        raise ProtocolError('Invalid captured background configuration')
    runtime = runtime_settings(settings['runtime'])
    if settings['lsf'] is None:
        return LocalExecutionBackend(root, runtime)
    return LsfExecutionBackend(root, runtime, lsf_settings(settings['lsf']))
