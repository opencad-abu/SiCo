"""Plan source-backed objective enrichment without importing usage decisions."""

from copy import deepcopy

from .jsonio import fail, fingerprint
from .patches import node, pointer, tokens
from .provenance import source_for


def unresolved(value):
    return isinstance(value, dict) and (value.get('state') == 'unknown' or value.get('kind') == 'unknown')


class Facts:
    def __init__(self, current, source, *, preserve_conflicts=False):
        self.current, self.source = current, source
        self.documents, self.dependencies, self.fields = {}, {}, []
        self.originals, self.incoming = {}, {}
        self.sources = deepcopy(current.document('sources.json'))
        self.source_ids = {}
        self.preserve_conflicts, self.conflicts = preserve_conflicts, []

    def source_document(self, name):
        if name not in self.incoming:
            self.incoming[name] = self.source.document(name)
        return self.incoming[name]

    def original(self, name):
        if name not in self.originals:
            self.originals[name] = self.current.document(name)
        return self.originals[name]

    def document(self, name):
        if name not in self.documents:
            self.documents[name] = deepcopy(self.original(name))
        return self.documents[name]

    def provenance(self, ref):
        if ref in self.source_ids:
            return self.source_ids[ref]
        record = deepcopy(self.source_document('sources.json')['items'][ref])
        if record['kind'] == 'user':
            fail('Objective facts cannot inherit user decisions', 'pdk_fact_authority')
        if 'inputs' in record:
            record['inputs'] = [self.provenance(child) for child in record['inputs']]
        if record['kind'] == 'document':
            resource = self.source_document('file.json')['items'][record['file_ref']]
            if not resource.get('dependency'):
                fail('Document facts require a byte dependency', 'pdk_fact_authority')
            self.dependency(resource['dependency'])
        ident = 'fact_' + fingerprint(record)[7:31]
        if ident in self.sources['items'] and self.sources['items'][ident] != record:
            fail('Fact provenance collision', 'pdk_update_conflict')
        self.sources['items'][ident] = record
        self.source_ids[ref] = ident
        return ident

    def dependency(self, ref):
        record = self.source.manifest['dependencies'][ref]
        old = self.current.manifest['dependencies'].get(ref)
        if old is not None and old != record:
            fail('Fact dependency conflicts: ' + ref, 'pdk_update_conflict')
        self.dependencies[ref] = deepcopy(record)

    def source_dependencies(self, ref):
        source = self.source_document('sources.json')['items'][ref]
        refs = set()
        if source['kind'] == 'document':
            refs.add(self.source_document('file.json')['items'][source['file_ref']]['dependency'])
        for child in source.get('inputs', []):
            refs.update(self.source_dependencies(child))
        return refs

    def evidence(self, name, path, source):
        document = self.document(name)
        evidence = document.setdefault('evidence', {})
        for old in list(evidence):
            if old == path or old.startswith(path + '/'):
                del evidence[old]
        evidence[path] = self.provenance(source)
        refs = set(self.source_document(name)['depends_on']) | self.source_dependencies(source)
        for ref in refs:
            self.dependency(ref)
        document['depends_on'] = sorted(set(document['depends_on']) | set(refs))

    def field(self, name, path):
        incoming = self.source_document(name)
        value = node(incoming, path)
        if unresolved(value):
            return
        source = source_for(incoming, path)
        source_kind = self.source_document('sources.json')['items'][source]['kind']
        if source_kind == 'user':
            return
        current = self.document(name)
        parts = tokens(path)
        parent = current
        for part in parts[:-1]:
            if part not in parent:
                fail('Fact target does not exist: ' + name + path, 'pdk_update_conflict')
            parent = parent[part]
        old = parent.get(parts[-1])
        if old == value:
            return
        # A documented definition can refine a raw CDF prompt. Other known facts
        # require explicit conflict resolution, including known physical units.
        prior = self.original('sources.json')['items'].get(source_for(current, path), {})
        refinement = parts[-1] == 'meaning' and prior.get('kind') == 'session' and source_kind == 'document'
        if old is not None and not unresolved(old) and not refinement:
            if self.preserve_conflicts:
                self.conflicts.append({'file': name, 'path': path, 'current': old, 'incoming': value,
                                       'source': source, 'resolution': 'preserved_current'})
                return
            fail('Known fact conflicts: ' + name + path, 'pdk_update_conflict')
        parent[parts[-1]] = deepcopy(value)
        self.evidence(name, path, source)
        self.fields.append({'file': name, 'path': path})

    def full(self, name):
        incoming = deepcopy(self.source_document(name))
        refs = set(incoming['depends_on'])
        for source in {incoming['source'], *incoming.get('evidence', {}).values()}:
            self.provenance(source)
            refs.update(self.source_dependencies(source))
        incoming['depends_on'] = sorted(refs)
        incoming['source'] = self.provenance(incoming['source'])
        incoming['evidence'] = {p: self.provenance(s) for p, s in incoming.get('evidence', {}).items()}
        for ref in incoming['depends_on']:
            self.dependency(ref)
        if self.current.available(name):
            existing = {**self.original(name), 'depends_on': sorted(self.original(name)['depends_on'])}
            if existing != incoming:
                fail('Existing fact document conflicts: ' + name, 'pdk_update_conflict')
            return
        self.documents[name] = incoming
        self.fields.append({'file': name, 'path': ''})

    def resources(self):
        target = self.document('file.json')
        incoming = self.source_document('file.json')
        for key, row in incoming['items'].items():
            old = target['items'].get(key)
            if old is not None and any(old.get(k) != row.get(k) for k in ('root', 'path')):
                fail('Resource identity conflicts: ' + key, 'pdk_update_conflict')
            if old and old['kind'] != row['kind']:
                prior = self.current.document('sources.json')['items'].get(
                    source_for(self.original('file.json'), pointer('items', key)), {})
                referenced = self.current.available('model.json') and any(
                    i['file_ref'] == key for c in self.current.document('model.json')['configs'].values()
                    for i in c['includes'])
                if old.get('dependency') or prior.get('kind') != 'session' or referenced or not row.get('dependency'):
                    fail('Verified resource classification conflicts: ' + key, 'pdk_update_conflict')
            if row.get('dependency'):
                self.dependency(row['dependency'])
            if old == row:
                continue
            if old and old.get('dependency') and old['dependency'] != row.get('dependency'):
                fail('Resource dependency conflicts: ' + key, 'pdk_update_conflict')
            target['items'][key] = deepcopy(row)
            self.evidence('file.json', pointer('items', key), source_for(incoming, pointer('items', key)))
            self.fields.append({'file': 'file.json', 'path': pointer('items', key)})

    def plan(self):
        if (self.source.manifest['package_id'] != self.current.manifest['package_id'] or
                self.source.manifest['libraries'] != self.current.manifest['libraries']):
            fail('Source library identities differ', 'pdk_update_conflict')
        self.resources()
        old_rows = self.source_document('device.json')['items']
        rows = self.original('device.json')['items']
        for key, row in old_rows.items():
            if key not in rows or any(row.get(k) != rows[key].get(k) for k in ('library', 'cell', 'view', 'dir')):
                fail('Source device inventory differs: ' + key, 'pdk_update_conflict')
            for field in ('voltage', 'limits', 'note'):
                if field in row:
                    self.field('device.json', pointer('items', key, field))
            if 'dir' not in row:
                continue
            name = row['dir'] + '/cdf.json'
            incoming = self.source_document(name)
            if not any(self.source.manifest['dependencies'][d]['kind'] == 'cdf' for d in incoming['depends_on']):
                fail('CDF facts require a source CDF dependency', 'pdk_fact_authority')
            if set(incoming['parameters']) != set(self.original(name)['parameters']):
                fail('Source CDF inventory differs: ' + key, 'pdk_update_conflict')
            # Explicit objective evidence is the import boundary. A cached raw
            # default or an inherited user rule is not a new documentation fact.
            for path in incoming.get('evidence', {}):
                parts = tokens(path)
                if len(parts) == 3 and parts[0] == 'parameters' and parts[2] in {
                        'type', 'unit', 'meaning', 'default', 'domain'}:
                    self.field(name, path)
            name = row['dir'] + '/simulation.json'
            if self.source.available(name):
                if not any(self.source.manifest['dependencies'][d]['kind'] == 'interface'
                           for d in self.source_document(name)['depends_on']):
                    fail('Simulation facts require an interface dependency', 'pdk_fact_authority')
                self.full(name)
        if self.source.available('model.json'):
            self.full('model.json')
        self.documents['sources.json'] = self.sources
        self.documents = {n: v for n, v in self.documents.items()
                          if not self.current.available(n) or v != self.original(n)}
        return self
