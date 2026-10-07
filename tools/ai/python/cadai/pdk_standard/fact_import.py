"""Generation-only, source-checked reuse of objective facts from a standard package."""

from copy import deepcopy

from sicostate import project_directory

from ..pdk_normalize import context, detail
from . import VERSION
from .fact_merge import Facts
from .jsonio import fail, fingerprint
from .package import Package, locked
from .provenance import verify
from .workspace import Effective, entries, select


def source_package(path, library, revision):
    rows = entries(path)
    bases = [(p, e) for p, e, m in rows if m.get('format') == 'sico.pdk.package'
             and any(v['name'] == library for v in Package(p).manifest['libraries'].values())]
    if len(bases) != 1:
        fail('Fact import requires exactly one source baseline', 'pdk_update_conflict')
    path, entry = bases[0]
    base = Package(path).verify()
    overlays = [(p, e) for p, e, m in rows if m.get('base', {}).get('package_id') == base.manifest['package_id']]
    if len(overlays) > 1:
        fail('Fact import source has competing overlays', 'pdk_update_conflict')
    roots = {**entry['roots'], **(overlays[0][1]['roots'] if overlays else {})}
    result = verify(Effective(base, roots, overlays[0][0] if overlays else None))
    if result.revision != revision:
        fail('Fact source revision changed', 'pdk_update_conflict')
    return result


class Candidate:
    def __init__(self, current, plan):
        self.current, self.plan = current, plan
        self.roots = current.roots
        self.manifest = deepcopy(current.manifest)
        self.manifest['dependencies'].update(plan.dependencies)

    def available(self, name):
        return name in self.plan.documents or self.current.available(name)

    def document(self, name):
        return self.plan.documents[name] if name in self.plan.documents else self.current.document(name)


def validate_sources(candidate, plan, bridge, evidence, workspace):
    from .resource_check import check
    from .source_check import observed
    rows = candidate.document('device.json')['items']
    groups = {}
    for ref, dep in plan.dependencies.items():
        if dep['kind'] in {'file', 'model'}:
            check(candidate, ref)
            continue
        target = dep['target']
        matches = [key for key, row in rows.items() if all(row.get(k) == target.get(k)
                   for k in ('library', 'cell', 'view'))]
        if len(matches) != 1:
            fail('Fact dependency target is unresolved: ' + ref, 'pdk_source_changed')
        groups.setdefault(matches[0], set()).add(ref)
    if groups and bridge is None:
        fail('Fact reuse requires current OA source validation', 'pdk_source_unavailable')
    for key, refs in sorted(groups.items()):
        row = rows[key]
        library = candidate.manifest['libraries'][row['library']]['name']
        capture = bridge.capture('device', {'library': library, 'cell': row['cell'], 'view': row['view']})
        # Raw captures are private evidence, outside bounded standard JSON.
        from ..pdk_data import PdkData
        PdkData(workspace, {}).write(evidence / (key + '.json'), capture)
        value = detail(capture['data'], context(capture['context']))
        if (value.get('master_state') or {}).get('modified') is not False:
            fail('Save the source master before fact import', 'pdk_source_changed')
        # Preserve all existing dependencies for an affected cell, not only
        # those proposed by the historical source.
        for suffix in ('cdf.json', 'symbol.json', 'simulation.json'):
            name = row.get('dir', '') + '/' + suffix
            if candidate.available(name):
                refs.update(candidate.document(name)['depends_on'])
        observed(candidate, key, value, refs)
    return len(groups)


class ImportFacts:
    def __init__(self, workspace, environment, bridge):
        self.workspace, self.environment, self.bridge = workspace, environment, bridge

    def run(self, args):
        current = verify(select(self.workspace, args['library'], self.environment))
        if current.revision != args['revision']:
            fail('Effective revision changed', 'pdk_update_conflict')
        source = source_package(args['source_index'], args['library'], args['source_revision'])
        plan = Facts(current, source, preserve_conflicts=args.get('preserve_conflicts', False)).plan()
        if not plan.documents:
            return self.response(current.revision, 0, 0, None, len(plan.conflicts))
        candidate = verify(Candidate(current, plan))
        token = fingerprint([current.revision, source.revision])[7:31]
        evidence = project_directory(self.workspace, 'ai/pdk-evidence/facts-' + token, create=True)
        captures = validate_sources(candidate, plan, self.bridge, evidence, self.workspace)
        root = project_directory(self.workspace, 'ai/pdk-data', create=True)
        from .overlay import publish
        from .ingest import documents, _authority
        before = documents(current)
        _authority(before, {**before, **plan.documents})
        with locked(root):
            if select(self.workspace, args['library'], self.environment).revision != current.revision:
                fail('Concurrent fact update; read again', 'pdk_update_conflict')
            # Recheck source index identity after live captures and before commit.
            source_package(args['source_index'], args['library'], args['source_revision'])
            from .resource_check import check
            for ref, dep in plan.dependencies.items():
                if dep['kind'] in {'file', 'model'}:
                    check(candidate, ref)
            receipt = {'source_revision': source.revision, 'base_revision': current.revision,
                       'fields': plan.fields, 'dependencies': sorted(plan.dependencies),
                       'live_captures': captures, 'usage_rules_imported': False, 'conflicts': plan.conflicts}
            from ..pdk_data import PdkData
            PdkData(self.workspace, self.environment).write(evidence / 'receipt.json', receipt)
            result = publish(root, current, plan.documents, dependencies=plan.dependencies)
        return self.response(result['revision'], len(plan.fields), captures, evidence / 'receipt.json', len(plan.conflicts))

    @staticmethod
    def response(revision, count, captures, receipt, conflicts):
        return {'schema_version': VERSION, 'status': 'incomplete' if conflicts else 'ok', 'revision': revision,
                'imported_fields': count, 'live_captures': captures,
                'preserved_conflicts': conflicts,
                'evidence_ref': str(receipt) if receipt else None,
                'usage_rules_imported': False, 'callbacks_executed': False, 'oa_writes': False,
                'next_action': 'get_pdk_data', 'details': {'section': 'collection'}}
