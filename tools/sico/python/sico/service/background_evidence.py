"""Identity-bound readers for retained background worker evidence."""

from pathlib import Path

from ..transport.framing import strict_json
from .background_watchdog import boot_id, process_alive

CUSTODY_PROTOCOL = 'cad_ai_background_custody.v1'
WATCH_IDENTITY = ('job_id', 'worker_id', 'instance_id', 'generation')


def same_identity(actual, expected):
    """Compare the kernel identity fields that survive a persisted PID."""
    return bool(isinstance(actual, dict) and isinstance(expected, dict)
                and actual.get('pid') == expected.get('pid')
                and actual.get('start') == expected.get('start'))


def owned_record(directory, name, identity):
    """Read one private record only when its job identity matches."""
    from .background_worker import _read

    try:
        record = strict_json(_read(Path(directory) / name))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    if any(record.get(k) != identity.get(k) for k in WATCH_IDENTITY):
        return None
    return record


def watchdog_record(directory, identity):
    return owned_record(directory, 'watchdog.json', identity)


def custody_record(directory, identity):
    """Current-boot custody evidence, including an armed (unfinished) report."""
    report = owned_record(directory, 'custody.json', identity)
    if (not report or report.get('protocol') != CUSTODY_PROTOCOL
            or report.get('boot_id') != boot_id()
            or report.get('tree_owned') is not True):
        return None
    return report


def custody_proof(directory, identity, custodian=None, watcher=None):
    """Finished whole-tree proof: no owned children remain."""
    report = custody_record(directory, identity)
    if (not report or report.get('phase') != 'finished'
            or report.get('process_stopped') is not True
            or type(report.get('children_remaining')) is not int
            or report.get('children_remaining') != 0):
        return None
    if custodian is not None and not same_identity(report.get('custodian'), custodian):
        return None
    if watcher is not None and not same_identity(report.get('watcher'), watcher):
        return None
    launcher = report.get('launcher')
    if launcher and process_alive(launcher):
        return None
    return report
