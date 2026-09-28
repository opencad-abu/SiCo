"""Track retained scheduler uncertainty without filesystem waits in lifecycle reads."""

from ..transport.framing import ProtocolError, strict_json
from .background_worker import _read
from .execution_backend import backend_kind


def pending(row):
    if backend_kind(row) != 'lsf':
        return False
    if row['state'] in {'cancelled', 'queue_timeout'} and row.get('operation_dispatched') is False:
        return False
    return not (row['state'] == 'completed' and row.get('scheduler_verified') is True)



def scan(records):
    unresolved = set()
    for path in records.glob('*.json'):
        try:
            row = strict_json(_read(path))
            if row.get('job_id') != path.stem:
                raise ProtocolError('Invalid retained job identity')
            if pending(row):
                unresolved.add(path.stem)
        except (OSError, ValueError, KeyError):
            unresolved.add(path.stem)  # Damage cannot prove that external execution ended.
    return unresolved
