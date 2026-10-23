"""Verifier-side snapshots: capture before operations, verify again at completion."""

from copy import deepcopy

from .acceptance_subject import capture, device_dependencies
from .jsonio import fail
from .receipts import OPERATIONS, SCHEMA, VERIFIER, allow_devices, run_shape


def begin(package, devices, source_validator):
    """Freeze the tested revision only after independently checking its sources.

    source_validator is supplied by the real verifier (for example the existing
    check_publication bridge). This does not execute or certify the operations.
    """
    if (not isinstance(devices, (list, tuple)) or not devices
            or any(not isinstance(device, str) for device in devices)
            or len(devices) != len(set(devices)) or set(devices) - allow_devices(package)):
        fail('Acceptance must name unique declared usable devices')
    if not callable(source_validator):
        fail('Acceptance requires independent source validation')
    source_validator(package)
    from .resource_check import check
    for ref, dependency in package.manifest['dependencies'].items():
        if dependency['kind'] in {'file', 'model'}:
            check(package, ref)
    return {'subject': capture(package), 'devices': sorted(devices),
            'fingerprints': {device: device_dependencies(package, device)
                             for device in sorted(devices)}}


def finish(package, snapshot, results, *, at, run_ref, source_validator,
           evidence_ref=None, operations=OPERATIONS):
    """Record explicit results against a freshly selected post-run package.

    Never obtain a new identity for an old run. The caller keeps the beginning
    snapshot with raw evidence and supplies actual device/operation outcomes.
    """
    current = begin(package, snapshot['devices'], source_validator)
    if current != snapshot:
        fail('Acceptance subject or sources changed during the run', 'pdk_acceptance_required')
    run = {**deepcopy(snapshot), 'operations': list(operations), 'results': deepcopy(results),
           'at': at, 'run_ref': run_ref}
    if evidence_ref is not None:
        run['evidence_ref'] = evidence_ref
    run_shape(run)
    return run


def evidence(runs):
    """Assemble already-bound runs; compilation does not inspect current data."""
    if not runs:
        fail('Acceptance evidence has no runs')
    for run in runs:
        run_shape(run)
    return {'schema_version': SCHEMA, 'verifier': VERIFIER, 'runs': deepcopy(runs)}
