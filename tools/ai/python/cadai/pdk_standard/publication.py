"""Immutable publication of an accepted generation revision, with atomic CAS."""

from copy import deepcopy
from pathlib import Path
import os
import shutil
import tempfile
import uuid

from sicostate import project_directory
from ..pdk_normalize import now
from . import VERSION
from .jsonio import Writer, atomic, encode, fail, fingerprint, mapping, read, relative, TARGET
from .package import Package, locked

FEATURE = 'qualified-publication-v1'
PROFILE = 'circuit-simulation-v1'


def validate_manifest(manifest):
    from .validate import fields
    from .validate_package import DIGEST, text
    features = manifest.get('required_features', [])
    if (not isinstance(features, list) or any(not isinstance(f, str) for f in features)
            or len(features) != len(set(features))):
        fail('Invalid required standard features')
    if set(features) - {FEATURE}:
        fail('Unsupported required standard feature', 'unsupported_pdk_standard')
    publication = manifest.get('publication')
    if (FEATURE in features) != (publication is not None):
        fail('Publication requires its feature marker')
    if publication is not None:
        fields(publication, ('profile', 'at', 'content_digest', 'based_on'))
        if publication['profile'] != PROFILE:
            fail('Unsupported PDK publication profile', 'unsupported_pdk_standard')
        text(publication['at'])
        for key in ('content_digest', 'based_on'):
            if not isinstance(publication[key], str) or not DIGEST.fullmatch(publication[key]):
                fail('Invalid publication digest')


def invalidate(manifest):
    manifest.pop('publication', None)
    manifest['required_features'] = [f for f in manifest.get('required_features', []) if f != FEATURE]


def content_digest(manifest):
    # Bind the immutable file inventory; individual on-demand reads check bytes.
    return fingerprint({k: manifest[k] for k in
                        ('package_id', 'pdk_version', 'options', 'libraries', 'dependencies', 'files')})


def require(package):
    validate_manifest(package.manifest)
    publication = package.manifest.get('publication')
    if publication is None:
        fail('PDK generation incomplete: complete and publish circuit/simulation data in the '
             'PDK generation workflow. Normal design only reads published data.', 'pdk_generation_incomplete')
    if publication['content_digest'] != content_digest(package.manifest):
        fail('Published PDK content changed; generation acceptance must be repeated', 'pdk_update_conflict')


def publish(workspace, library, revision, environment=None, *, source_validator=None):
    """Validate the exact current revision before changing any reader-visible index."""
    from .ingest import documents, write_documents
    from .provenance import verify
    from .readiness import accept
    from .workspace import select, Effective
    root = project_directory(workspace, 'ai/pdk-data', create=True)
    with locked(root):
        current = select(workspace, library, environment)
        if current.revision != revision:
            fail('Generation revision changed; read again', 'pdk_update_conflict')
        verify(current)
        if 'publication' in current.manifest:
            require(current)
            return result(current)
        accept(current)
        if source_validator:
            source_validator(current)
        manifest = deepcopy(current.manifest)
        manifest.update(schema_version=VERSION, revision=uuid.uuid4().hex, created_at=now(),
                        required_features=[FEATURE])
        predecessor = current.base.manifest_digest
        if current.base.root.parent == root:
            predecessor = current.base.manifest.get('publication', {}).get('based_on', predecessor)
        manifest['publication'] = {
            'profile': PROFILE, 'at': manifest['created_at'], 'content_digest': content_digest(current.manifest),
            'based_on': predecessor}
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
            if select(workspace, library, environment).revision != revision:
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
    return {'schema_version': VERSION, 'status': 'published', 'revision': package.revision,
            'path': str(package.base.root / 'package.json'), 'profile': PROFILE, 'design_ready': True,
            'device_count': sum(any(r['use'][p] == 'allow' for p in ('circuit', 'testbench')) for r in rows),
            'next_action': 'get_pdk_data'}
