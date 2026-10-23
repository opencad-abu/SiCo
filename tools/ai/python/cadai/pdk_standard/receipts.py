"""Versioned acceptance evidence: publication checks recorded facts, never fills them."""

from copy import deepcopy
from pathlib import Path

from ..json_values import strict_json
from .acceptance_subject import capture, data_digest, device_dependencies, identity_dependencies
from .jsonio import TARGET, encode, fail, fingerprint, sha

VERIFIER = 'sico-device-acceptance-v2'
FEATURE = 'verified-acceptance-v2'
SCHEMA = 'sico.pdk.acceptance-evidence.v2'
# Legacy packages remain inspectable; design use requires a fresh v2 run.
LEGACY_FEATURE = 'verified-acceptance-v1'
LEGACY_VERIFIER = 'sico-device-acceptance-v1'
OPERATIONS = ('creation', 'parameter_roundtrip', 'wiring', 'netlist', 'simulation_config')
LIMIT = 256 * 1024


def load(path):
    """Read bounded strict JSON; v1 evidence cannot acquire a tested identity."""
    target = Path(path)
    try:
        with target.open('rb') as stream:
            raw = stream.read(LIMIT + 1)
    except OSError as exc:
        fail('Acceptance evidence is unreadable: ' + str(exc), 'pdk_source_unavailable')
    if len(raw) > LIMIT:
        fail('Acceptance evidence exceeds its bound')
    try:
        value = strict_json(raw)
    except ValueError as exc:
        fail('Acceptance evidence is not strict JSON: ' + str(exc))
    if value.get('schema_version') != SCHEMA:
        fail('Acceptance evidence requires v2 tested-content binding; rerun acceptance',
             'pdk_acceptance_required')
    if value.get('verifier') != VERIFIER:
        fail('Unregistered acceptance verifier', 'unsupported_pdk_standard')
    runs = value.get('runs')
    if not isinstance(runs, list) or not runs:
        fail('Acceptance evidence has no runs')
    for run in runs:
        run_shape(run)
    return {'path': str(target), 'digest': sha(raw), 'verifier': VERIFIER,
            'operations': list(OPERATIONS), 'runs': runs}


def _unique_strings(values):
    return (isinstance(values, list) and bool(values)
            and all(isinstance(value, str) and value for value in values)
            and len(values) == len(set(values)))


def run_shape(run, *, legacy=False):
    """Evidence and published receipts have the same required run fields."""
    from .acceptance_subject import validate
    from .validate import fields
    from .validate_package import DIGEST, text

    required = ('operations', 'devices', 'at', 'run_ref', 'fingerprints')
    fields(run, required if legacy else (*required, 'subject', 'results'), ('evidence_ref',))
    operations, devices = run['operations'], run['devices']
    if not _unique_strings(operations) or set(operations) - set(OPERATIONS):
        fail('Acceptance run operations are invalid')
    if not _unique_strings(devices):
        fail('Acceptance run devices are invalid')
    for key in ('at', 'run_ref', 'evidence_ref'):
        if key in run:
            text(run[key], 4096)
    fingerprints = run['fingerprints']
    if not isinstance(fingerprints, dict) or set(fingerprints) != set(devices):
        fail('Acceptance run fingerprints must cover the recorded devices')
    for mapping in fingerprints.values():
        if not isinstance(mapping, dict) or (not legacy and not mapping) or any(
                not isinstance(key, str) or not key or not isinstance(digest, str)
                or not DIGEST.fullmatch(digest) for key, digest in mapping.items()):
            fail('Acceptance run fingerprints are invalid')
    if legacy:
        return
    validate(run['subject'])
    results = run['results']
    if not isinstance(results, dict) or set(results) != set(devices):
        fail('Acceptance results must cover the recorded devices')
    for outcomes in results.values():
        if (not isinstance(outcomes, dict) or set(outcomes) != set(operations)
                or any(result not in ('passed', 'failed') for result in outcomes.values())):
            fail('Acceptance results must record every device/operation outcome')


def allow_devices(package):
    rows = package.document('device.json')['items']
    return {key for key, row in rows.items()
            if any(row['use'][purpose] == 'allow' for purpose in ('circuit', 'testbench'))}


def record(package, evidence):
    """Compare the tested snapshot and preserve it unchanged in publication."""
    allowed, subject, receipts = allow_devices(package), capture(package), {}
    for index, run in enumerate(evidence['runs'], 1):
        run_shape(run)
        unknown = sorted(set(run['devices']) - allowed)
        if unknown:
            fail('Acceptance covers unknown or non-allowed devices: ' + ', '.join(unknown[:5]))
        if run['subject'] != subject:
            fail('Acceptance subject is stale; test the current effective revision',
                 'pdk_acceptance_required')
        for device in run['devices']:
            if run['fingerprints'][device] != device_dependencies(package, device):
                fail('Acceptance source fingerprints are stale for: ' + device,
                     'pdk_acceptance_required')
        if any(result != 'passed' for outcomes in run['results'].values()
               for result in outcomes.values()):
            fail('Acceptance contains failed operations', 'pdk_acceptance_incomplete')
        key = f'run_{index:02d}_{fingerprint(run)[7:15]}'
        if len(encode({key: run})) <= TARGET:
            receipts[key] = deepcopy(run)
        else:
            # Split only the object set; retain each tested identity and result.
            # The evidence digest and run_ref still refer to the original run.
            for number, device in enumerate(run['devices'], 1):
                row = deepcopy(run)
                row.update(devices=[device], fingerprints={device: row['fingerprints'][device]},
                           results={device: row['results'][device]})
                receipts[f'{key}_{number:04d}'] = row
    return {'verifier': evidence['verifier'], 'operations': list(OPERATIONS),
            'evidence_ref': evidence['path'], 'evidence_digest': evidence['digest'],
            'receipts': receipts}


def gaps(package, block):
    """Missing or failed device/operation pairs; failures cannot be masked."""
    covered, failed = {}, set()
    for row in block['receipts'].values():
        for device in row['devices']:
            passed = {operation for operation, result in row.get('results', {}).get(device, {}).items()
                      if result == 'passed'}
            covered.setdefault(device, set()).update(passed & set(row['operations']))
            failed.update((device, operation) for operation, result in
                          row.get('results', {}).get(device, {}).items() if result == 'failed')
    return sorted(failed | {(device, operation) for device in allow_devices(package)
                           for operation in set(OPERATIONS) - covered.get(device, set())})


def stale(package, block):
    """Check tested data and dependency bindings against the published package."""
    allowed, digest, changed = allow_devices(package), data_digest(package.manifest), set()
    for row in block['receipts'].values():
        subject = row.get('subject', {})
        for device in row['devices']:
            if (device not in allowed or subject.get('content_digest') != digest
                    or subject.get('package_id') != package.manifest['package_id']
                    or identity_dependencies(package, device) - row['fingerprints'][device].keys()
                    or any(package.manifest['dependencies'].get(ref, {}).get('fingerprint') != value
                           for ref, value in row['fingerprints'].get(device, {}).items())):
                changed.add(device)
    return sorted(changed)
