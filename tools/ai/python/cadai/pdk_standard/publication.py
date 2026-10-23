"""Immutable publication of an accepted generation revision, with atomic CAS."""

import os
import shutil
import tempfile
import uuid
from copy import deepcopy
from pathlib import Path

from sicostate import project_directory

from ..pdk_normalize import now
from . import VERSION
from .jsonio import TARGET, Writer, atomic, encode, fail, fingerprint, mapping, read, relative
from .package import Package, locked

FEATURE = 'qualified-publication-v1'
PROFILE = 'circuit-simulation-v1'


def validate_manifest(manifest):
    from .receipts import FEATURE as ACCEPTANCE
    from .receipts import OPERATIONS, VERIFIER, LEGACY_FEATURE, LEGACY_VERIFIER
    from .validate import fields
    from .validate_package import DIGEST, text
    features = manifest.get('required_features', [])
    if (not isinstance(features, list) or any(not isinstance(f, str) for f in features)
            or len(features) != len(set(features))):
        fail('Invalid required standard features')
    if set(features) - {FEATURE, ACCEPTANCE, LEGACY_FEATURE}:
        fail('Unsupported required standard feature', 'unsupported_pdk_standard')
    publication = manifest.get('publication')
    if (FEATURE in features) != (publication is not None):
        fail('Publication requires its feature marker')
    markers = set(features) & {ACCEPTANCE, LEGACY_FEATURE}
    if len(markers) > 1:
        fail('Conflicting acceptance versions')
    if markers and FEATURE not in features:
        fail('Acceptance receipts require the publication feature')
    if publication is not None:
        fields(publication, ('profile', 'at', 'content_digest', 'based_on'),
               ('acceptance', 'scope'))
        if publication['profile'] != PROFILE:
            fail('Unsupported PDK publication profile', 'unsupported_pdk_standard')
        text(publication['at'])
        for key in ('content_digest', 'based_on'):
            if not isinstance(publication[key], str) or not DIGEST.fullmatch(publication[key]):
                fail('Invalid publication digest')
        scope = publication.get('scope')
        if scope is not None:
            fields(scope, ('covers', 'libraries', 'devices', 'simulators', 'corners',
                           'unfinished'))
            if scope['covers'] != 'data':
                fail('Publication scope covers data only')
            for key in ('libraries', 'simulators', 'corners', 'unfinished'):
                values = scope[key]
                if (not isinstance(values, list)
                        or any(not isinstance(v, str) or not v or len(v) > 64 for v in values)
                        or len(values) != len(set(values))):
                    fail('Invalid publication scope list: ' + key)
            if not scope['libraries'] or not scope['simulators']:
                fail('Publication scope needs libraries and simulators')
            if type(scope['devices']) is not int or scope['devices'] < 1:
                fail('Publication scope needs a device count')
        acceptance = publication.get('acceptance')
        if bool(markers) != (acceptance is not None):
            fail('Acceptance receipts require their feature marker')
        if acceptance is not None:
            fields(acceptance, ('verifier', 'operations', 'evidence_ref', 'evidence_digest',
                                'receipts'))
            legacy = LEGACY_FEATURE in markers
            if acceptance['verifier'] != (LEGACY_VERIFIER if legacy else VERIFIER):
                fail('Unregistered acceptance verifier', 'unsupported_pdk_standard')
            if acceptance['operations'] != list(OPERATIONS):
                fail('Acceptance operations differ from the registered set')
            text(acceptance['evidence_ref'])
            if not isinstance(acceptance['evidence_digest'], str) or not DIGEST.fullmatch(
                    acceptance['evidence_digest']):
                fail('Invalid acceptance evidence digest')
            if not isinstance(acceptance['receipts'], dict) or not acceptance['receipts']:
                fail('Acceptance requires recorded receipts')
            from .receipts import run_shape
            for key, row in acceptance['receipts'].items():
                from .validate import identifier
                identifier(key)
                run_shape(row, legacy=legacy)


def invalidate(manifest):
    from .receipts import FEATURE as ACCEPTANCE, LEGACY_FEATURE
    manifest.pop('publication', None)
    manifest['required_features'] = [f for f in manifest.get('required_features', [])
                                     if f not in {FEATURE, ACCEPTANCE, LEGACY_FEATURE}]


def content_digest(manifest):
    # Bind the immutable file inventory; individual on-demand reads check bytes.
    return fingerprint({k: manifest[k] for k in
                        ('package_id', 'pdk_version', 'options', 'libraries', 'dependencies', 'files')})


def require(package):
    from .receipts import FEATURE as ACCEPTANCE
    from .receipts import gaps, stale
    validate_manifest(package.manifest)
    publication = package.manifest.get('publication')
    if publication is None:
        fail('PDK generation incomplete: complete and publish circuit/simulation data in '
             'the PDK generation workflow. Normal design only reads published data.',
             'pdk_generation_incomplete')
    if publication['content_digest'] != content_digest(package.manifest):
        fail('Published PDK content changed; generation acceptance must be repeated', 'pdk_update_conflict')
    features = package.manifest.get('required_features', [])
    if ACCEPTANCE not in features or 'acceptance' not in publication:
        fail('Published PDK lacks verified-acceptance-v2 tested-content receipts; run device acceptance '
             'and publish again before design use', 'pdk_acceptance_required')
    missing = gaps(package, publication['acceptance'])
    if missing:
        sample = ', '.join(f'{device}:{operation}' for device, operation in missing[:5])
        fail('Acceptance coverage is incomplete for the declared devices '
             f'({len(missing)} missing, e.g. {sample})', 'pdk_acceptance_required')
    changed = stale(package, publication['acceptance'])
    if changed:
        fail('Acceptance is stale for: ' + ', '.join(changed[:5]), 'pdk_acceptance_required')


def publication_scope(package, unfinished):
    """The declared support scope of one publication: data only, no software."""
    from .simulation_interfaces import TARGETS
    rows = package.document('device.json')['items']
    allow = [row for row in rows.values()
             if any(row['use'][purpose] == 'allow' for purpose in ('circuit', 'testbench'))]
    simulators, corners = set(), set()
    for row in allow:
        name = row.get('dir', '') + '/simulation.json'
        if not row.get('dir') or not package.available(name):
            continue
        simulation = package.document(name)
        if not simulation['interfaces']:
            # An interface-free document is collected only with the full probe
            # basis: the device was recorded netlist-invisible under every
            # registered target simulator, so that set is its coverage.
            simulators.update(TARGETS)
            continue
        for interface in simulation['interfaces'].values():
            simulators.add(interface['simulator'])
            corners.update(interface.get('model_refs') or [])
    libraries = sorted({package.manifest['libraries'][row['library']]['name'] for row in allow})
    return {'covers': 'data', 'libraries': libraries, 'devices': len(allow),
            'simulators': sorted(simulators), 'corners': sorted(corners),
            'unfinished': sorted(set(unfinished))}


def publish(workspace, library, revision, environment=None, *, source_validator=None,
            acceptance=None, unfinished=()):
    """Validate the exact current revision before changing any reader-visible index."""
    from .ingest import documents, write_documents
    from .provenance import verify
    from .readiness import accept
    from .receipts import FEATURE as ACCEPTANCE
    from .receipts import gaps, record
    from .receipts import load as load_acceptance
    from .workspace import Effective, select
    root = project_directory(workspace, 'ai/pdk-data', create=True)
    with locked(root):
        current = select(workspace, library, environment)
        if current.revision != revision:
            fail('Generation revision changed; read again', 'pdk_update_conflict')
        verify(current)
        if ('publication' in current.manifest
                and ACCEPTANCE in current.manifest.get('required_features', [])):
            require(current)
            return result(current)
        accept(current)
        if acceptance is None:
            fail('PDK acceptance evidence is required before publication; run the device '
                 'acceptance (creation, parameter roundtrip, wiring, netlist, simulation '
                 'configuration) and pass its record', 'pdk_acceptance_required')
        block = record(current, load_acceptance(acceptance))
        tested = next(iter(block['receipts'].values()))['subject']
        missing = gaps(current, block)
        if missing:
            sample = ', '.join(f'{device}:{operation}' for device, operation in missing[:5])
            fail('Acceptance coverage is incomplete for the declared devices '
                 f'({len(missing)} missing, e.g. {sample})', 'pdk_acceptance_incomplete')
        if source_validator:
            source_validator(current)
        manifest = deepcopy(current.manifest)
        manifest.update(schema_version=VERSION, revision=uuid.uuid4().hex, created_at=now(),
                        required_features=[FEATURE, ACCEPTANCE])
        predecessor = current.base.manifest_digest
        if current.base.root.parent == root:
            predecessor = current.base.manifest.get('publication', {}).get('based_on', predecessor)
        manifest['publication'] = {
            'profile': PROFILE, 'at': manifest['created_at'], 'content_digest': content_digest(current.manifest),
            'based_on': predecessor, 'scope': publication_scope(current, unfinished),
            'acceptance': block}
        stage = Path(tempfile.mkdtemp(prefix='.publication-', dir=root))
        try:
            write_documents(stage, manifest, documents(current))
            candidate = Effective(Package(stage), current.roots)
            require(candidate)
            accept(candidate)
            index_path = root / 'index.json'
            index = read(index_path) if index_path.exists() else {
                'format': 'sico.pdk.index', 'schema_version': VERSION, 'packages': {}}
            index['packages'] = mapping(root, index['packages'])
            key = manifest['package_id']
            for ident, entry in list(index['packages'].items()):
                old = read(relative(root, entry['path']) / 'package.json')
                if old.get('package_id') == key or old.get('base', {}).get('package_id') == key:
                    del index['packages'][ident]
            destination = key + '.' + manifest['revision'][:12]
            from .acceptance_subject import capture
            fresh = select(workspace, library, environment)
            if fresh.revision != revision or capture(fresh) != tested:
                fail('Generation baseline changed during acceptance', 'pdk_update_conflict')
            os.rename(stage, root / destination)
            index['schema_version'] = VERSION
            index['packages'][key] = {'path': destination, 'roots': current.roots}
            if len(encode(index['packages'])) > TARGET:
                index['packages'] = Writer(root).shard('index.' + manifest['revision'] + '.json',
                                                     'packages', index['packages'])
            atomic(index_path, index)
            return result(select(workspace, library, environment))
        finally:
            if stage.exists():
                shutil.rmtree(stage)


def result(package):
    rows = package.document('device.json')['items'].values()
    acceptance = package.manifest.get('publication', {}).get('acceptance')
    devices = sorted({device for row in (acceptance or {}).get('receipts', {}).values()
                      for device in row['devices']})
    scope = package.manifest.get('publication', {}).get('scope')
    return {'schema_version': VERSION, 'status': 'published', 'revision': package.revision,
            'path': str(package.base.root / 'package.json'), 'profile': PROFILE,
            'scope': scope,
            'design_ready': acceptance is not None,
            'acceptance': None if acceptance is None else {
                'verifier': acceptance['verifier'],
                'receipts': len(acceptance['receipts']), 'devices': len(devices)},
            'device_count': sum(any(r['use'][p] == 'allow' for p in ('circuit', 'testbench')) for r in rows),
            'next_action': 'get_pdk_data'}
