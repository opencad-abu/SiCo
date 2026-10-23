"""Stable tested-content identity, including effective overlays and source inputs."""

from .jsonio import fail, fingerprint

PROJECTION = 'sico.pdk.acceptance-subject.v1'
IDENTITY_FIELDS = ('package_id', 'pdk_version', 'options', 'libraries', 'dependencies')


def data_digest(manifest):
    """Bind data files, omitting only the reserved generated receipt-shard names.

    Receipt shards are checked by the publication digest, but cannot participate
    in the tested-content digest: they are produced after the acceptance run.
    Installation roots and generated revision/time are not data content.
    """
    files = {name: digest for name, digest in manifest['files'].items()
             if not (name.startswith('package.acceptance.') and name.endswith('.json'))}
    return fingerprint({'projection': PROJECTION,
                        **{key: manifest[key] for key in IDENTITY_FIELDS}, 'files': files})


def capture(package):
    """Capture the exact data layout publication will produce from effective data."""
    from .ingest import content_inventory, documents

    manifest = {**package.manifest,
                'files': content_inventory(package.manifest, documents(package))}
    return {'projection': PROJECTION, 'package_id': manifest['package_id'],
            'revision': package.revision, 'content_digest': data_digest(manifest)}


def validate(subject):
    from .validate import fields, identifier
    from .validate_package import DIGEST, text

    fields(subject, ('projection', 'package_id', 'revision', 'content_digest'))
    if subject['projection'] != PROJECTION:
        fail('Unsupported acceptance subject projection', 'unsupported_pdk_standard')
    identifier(subject['package_id'])
    text(subject['revision'])
    if not isinstance(subject['content_digest'], str) or not DIGEST.fullmatch(subject['content_digest']):
        fail('Invalid tested content digest')


def device_dependencies(package, device):
    """Dependency closure explicitly declared by this device's documents.

    Dependency IDs are opaque. Document references and shared resource references
    determine membership, never an ID naming convention.
    """
    row = package.document('device.json')['items'].get(device)
    if row is None or not row.get('dir'):
        fail('Acceptance device has no collected detail: ' + device)
    dependencies = package.manifest['dependencies']
    refs = identity_dependencies(package, device)
    for name in ('device.json',
                 *(row['dir'] + '/' + suffix for suffix in
                   ('cdf.json', 'symbol.json', 'simulation.json', 'properties.json'))):
        if package.available(name):
            refs.update(package.document(name).get('depends_on', []))
    # The subject digest already binds the complete shared dependency inventory.
    # begin/finish check every byte dependency, including nested model includes;
    # copying that inventory into every device would exceed bounded receipts.
    from .validate_package import DIGEST

    result = {}
    for ref in sorted(refs):
        dep = dependencies.get(ref, {})
        digest = dep.get('fingerprint')
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            fail('Acceptance requires a known source fingerprint: ' + ref, 'pdk_acceptance_required')
        result[ref] = digest
    return result


def identity_dependencies(package, device):
    """Manifest-only dependencies with a declared device or shared identity."""
    row = package.document('device.json')['items'][device]
    target = {key: row[key] for key in ('library', 'cell', 'view')}
    return {key for key, dep in package.manifest['dependencies'].items()
            if dep['kind'] in {'library', 'technology'} or dep['target'] == target}
