"""Effective-package references and user authority for design decisions."""

from .jsonio import fail
from .patches import node, pointer


def source_for(document, path):
    # Probe the exact pointer and its ancestors, deepest first. Scanning every
    # evidence row for every parameter made large incremental packages quadratic.
    evidence = document.get('evidence', {})
    while True:
        if path in evidence:
            return evidence[path]
        if not path:
            return document.get('source')
        path = path.rpartition('/')[0]


def confirmed(package, document, path, sources=None):
    if sources is None:
        sources = package.document('sources.json')['items']
    return sources.get(source_for(document, path), {}).get('kind') == 'user'


def verify(package):
    sources = package.document('sources.json')['items']
    dependencies = package.manifest['dependencies']
    devices = package.document('device.json')
    categories = package.document('category.json')['items']
    resources = package.document('file.json')['items']
    identities = set()
    names = ['device.json', 'category.json', 'file.json']
    for key, row in devices['items'].items():
        if row['library'] not in package.manifest['libraries']:
            fail('Unknown device library')
        identity = (row['library'], row['cell'], str(row['view']))
        if identity in identities:
            fail('Duplicate device identity')
        identities.add(identity)
        if isinstance(row['categories'], list) and set(row['categories']) - set(categories):
            fail('Unknown category reference')
        for use, policy in row['use'].items():
            if policy != 'unknown' and not confirmed(package, devices, pointer('items', key, 'use', use), sources):
                fail('Device usage requires user confirmation provenance')
        if 'dir' in row:
            for suffix in ('cdf.json', 'symbol.json', 'simulation.json', 'properties.json'):
                name = row['dir'] + '/' + suffix
                if package.available(name):
                    names.append(name)
            cdf = package.document(row['dir'] + '/cdf.json')
            if 'interface_parameters' in cdf and not confirmed(package, cdf, '/interface_parameters', sources):
                fail('CDF interface scope requires user confirmation provenance')
            for param, definition in cdf['parameters'].items():
                for field in ('write', 'requirement'):
                    if definition[field] != 'unknown' and not confirmed(package, cdf, pointer('parameters', param, field), sources):
                        fail('CDF policy requires user confirmation provenance')
    if package.available('model.json'):
        names.append('model.json')
        models = package.document('model.json')['configs']
        for config in models.values():
            for include in config['includes']:
                if resources.get(include['file_ref'], {}).get('kind') != 'model':
                    fail('Model include must reference a registered model resource')
    for resource_id, resource in resources.items():
        if 'dependency' in resource:
            dep = dependencies.get(resource['dependency'], {})
            if dep.get('kind') not in {'file', 'model'} or dep.get('target') != resource_id:
                fail('Resource dependency identity mismatch')
    active, visited = set(), set()
    def visit(ref):
        if ref not in sources or ref in active:
            fail('Missing or cyclic provenance input')
        if ref in visited:
            return
        active.add(ref)
        source = sources[ref]
        if source['kind'] == 'document' and source['file_ref'] not in resources:
            fail('Document source must reference a registered resource')
        for child in source.get('inputs', []):
            visit(child)
        active.remove(ref)
        visited.add(ref)
    for ref in sources:
        visit(ref)
    for name in names:
        document = package.document(name)
        if set(document['depends_on']) - set(dependencies):
            fail('Unknown dependency: ' + name)
        if document['source'] not in sources:
            fail('Unknown source: ' + name)
        for path, source in document.get('evidence', {}).items():
            node(document, path)
            if source not in sources:
                fail('Unknown evidence source: ' + name)
        if name.endswith('/simulation.json'):
            directory = name.rsplit('/', 1)[0]
            params = package.document(directory + '/cdf.json')['parameters']
            terminals = package.document(directory + '/symbol.json')['terminals']
            configs = package.document('model.json')['configs'] if package.available('model.json') else {}
            for interface in document['interfaces'].values():
                if set(interface['terminal_map']) != set(terminals):
                    fail('Simulator mapping must cover actual symbol terminals')
                if set(interface.get('model_refs', [])) - set(configs):
                    fail('Unknown simulator model configuration')
                if any(row.get('cdf') not in params for row in interface['parameters'].values() if 'cdf' in row):
                    fail('Unknown simulator CDF parameter')
    return package
