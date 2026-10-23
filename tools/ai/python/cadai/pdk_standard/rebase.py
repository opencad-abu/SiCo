"""Rewrite the effective package as a new baseline and rebind its delta atomically.

The reviewer (baseline.py) decides which identity fields and dependency
fingerprints change; this module performs the one mechanical rebase: a complete
baseline with the updated manifest, a byte-copied delta whose recorded
preconditions on refreshed dependencies are re-approved explicitly, every
logical document proven unchanged, and a single index switch for both entries.
Readers never silently accept a stale base digest.
"""

import os
import shutil
import tempfile
import uuid
from copy import deepcopy
from pathlib import Path

from ..pdk_normalize import now
from . import VERSION
from .ingest import documents, write_documents
from .jsonio import TARGET, Writer, atomic, encode, fail, mapping, read, relative, sha
from .package import Package
from .provenance import verify
from .workspace import Effective

MAX_REAPPROVED_RECORDED = 40


def reapproved_names(package, refreshed):
    """Delta changes whose recorded preconditions cite a refreshed dependency."""
    return sorted(name for name, change in getattr(package, 'changes', {}).items()
                  if set(change.get('dependencies', {})) & set(refreshed))


def rewrite(root, package, manifest, refreshed, reason=None):
    """Build the new baseline, rebind the delta, switch the index atomically."""
    base, overlay = package.base, package.overlay
    manifest = deepcopy(manifest)
    manifest.update(revision=uuid.uuid4().hex, created_at=now())
    base_stage = Path(tempfile.mkdtemp(prefix='.baseline-', dir=root))
    overlay_stage = Path(tempfile.mkdtemp(prefix='.rebase-', dir=root)) if overlay else None
    try:
        write_documents(base_stage, manifest, documents(base))
        base_digest = sha((base_stage / 'package.json').read_bytes())
        rebase_record, overlay_manifest, reapproved = None, None, []
        if overlay:
            files = mapping(overlay, read(overlay / 'package.json')['files'])
            for name, digest in files.items():
                if name == 'changes.json':
                    continue
                destination = relative(overlay_stage, name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(relative(overlay, name), destination)
                read(destination, digest)
            delta = read(overlay / 'changes.json', files['changes.json'])
            items = mapping(overlay, delta['items'])
            for name, change in items.items():
                preconditions = change.get('dependencies', {})
                touched = sorted(set(preconditions) & set(refreshed))
                if touched:
                    for ref in touched:
                        preconditions[ref] = refreshed[ref]['to']
                    reapproved.append(name)
            reapproved.sort()
            additions = mapping(overlay, delta.get('dependency_updates', {}))
            for ref, row in refreshed.items():
                if ref in additions:
                    if ref in base.manifest['dependencies']:
                        fail('Dependency cannot live in both baseline and delta: ' + ref)
                    additions[ref] = {**additions[ref], 'fingerprint': row['to']}
            writer = Writer(overlay_stage)
            writer.put('changes.json', {'items': items, 'dependency_updates': additions},
                       ('items', 'dependency_updates'))
            files = {**files, 'changes.json': writer.files['changes.json']}
            rebase_record = {
                'from': {'revision': base.revision, 'manifest_digest': base.manifest_digest},
                'at': now(),
                'refreshed_dependencies': {ref: {'from': row['from'], 'to': row['to'],
                                                 'kind': row['kind']}
                                           for ref, row in sorted(refreshed.items())},
                'reapproved_changes': reapproved[:MAX_REAPPROVED_RECORDED],
                'reapproved_truncated': len(reapproved) > MAX_REAPPROVED_RECORDED,
                **({'reason': reason} if reason else {}),
            }
            overlay_manifest = {
                'format': 'sico.pdk.overlay', 'schema_version': VERSION,
                'revision': uuid.uuid4().hex,
                'base': {'package_id': manifest['package_id'], 'revision': manifest['revision'],
                         'manifest_digest': base_digest},
                'created_at': now(), 'files': dict(files), 'x_rebase': rebase_record,
            }
            writer.put('package.json', overlay_manifest, ('files',), inventory=False)

        candidate = Effective(Package(base_stage), package.roots, overlay_stage)
        verify(candidate)
        before_docs = documents(package)
        after_docs = documents(candidate)
        if after_docs != before_docs:
            changed = sorted(name for name in set(before_docs) | set(after_docs)
                             if before_docs.get(name) != after_docs.get(name))
            fail('A baseline update must not change logical documents: '
                 + ', '.join(changed[:5]), 'pdk_update_conflict')
        rechecked = len(candidate.changes)

        base_destination = root / (manifest['package_id'] + '.' + manifest['revision'][:12])
        os.rename(base_stage, base_destination)
        overlay_destination = None
        if overlay_stage:
            destination = manifest['package_id'] + '.local.' + overlay_manifest['revision'][:12]
            overlay_destination = root / destination
            os.rename(overlay_stage, overlay_destination)
        switch_index(root, package, manifest, base_destination, overlay_destination)
        return {'rebase': rebase_record, 'reapproved': reapproved,
                'baseline_revision': manifest['revision'],
                'documents_compared': len(before_docs), 'changes_rechecked': rechecked}
    finally:
        for stage in (base_stage, overlay_stage):
            if stage is not None and stage.exists():
                shutil.rmtree(stage)


def switch_index(root, package, manifest, base_destination, overlay_destination):
    index_path = root / 'index.json'
    if not index_path.is_file():
        fail('Workspace PDK data index disappeared before the baseline update; run '
             'prepare_pdk_data and prepare the update again', 'pdk_standard_unavailable')
    index = read(index_path)
    index['packages'] = mapping(root, index['packages'])
    expected_base = {'package_id': package.base.manifest['package_id'],
                     'revision': package.base.revision,
                     'manifest_digest': package.base.manifest_digest}
    base_keys, overlay_keys = [], []
    for key, entry in index['packages'].items():
        directory = relative(root, entry['path'])
        record = read(directory / 'package.json')
        if (record.get('format') == 'sico.pdk.package'
                and record.get('package_id') == manifest['package_id']
                and sha((directory / 'package.json').read_bytes()) == package.base.manifest_digest):
            base_keys.append(key)
        if record.get('format') == 'sico.pdk.overlay' and record.get('base') == expected_base:
            overlay_keys.append(key)
    if len(base_keys) != 1:
        fail('Exact installed baseline required', 'pdk_update_conflict')
    if package.overlay and len(overlay_keys) != 1:
        fail('Workspace overlay entry is missing', 'pdk_update_conflict')
    index['packages'][base_keys[0]]['path'] = base_destination.name
    if overlay_destination:
        index['packages'][overlay_keys[0]]['path'] = overlay_destination.name
    index['schema_version'] = VERSION
    if len(encode(index['packages'])) > TARGET:
        index['packages'] = Writer(root).shard('index.' + manifest['revision'] + '.json',
                                               'packages', index['packages'])
    atomic(index_path, index)
