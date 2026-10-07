"""Collection gaps across every observed device, independent of usage decisions."""

from .patches import pointer
from .interface import collection_parameters


def unknowns(value, path=''):
    if isinstance(value, dict):
        if value.get('state') == 'unknown' or value.get('kind') == 'unknown' or value.get('mode') == 'unknown':
            yield path, value.get('reason', 'Fact has not been established')
        else:
            for key, child in sorted(value.items()):
                if key not in {'evidence', 'source', 'depends_on'}:
                    yield from unknowns(child, path + pointer(key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from unknowns(child, path + pointer(index))


def gaps(package, device=None, category=None):
    def issue(file, path, reason, key=None):
        return {'file': file, 'path': path, 'reason': reason, 'device': key,
                'scope': 'fact_collection', 'action': 'collect_source_facts'}

    if not device:
        for field in ('pdk_version', 'options'):
            for path, reason in unknowns(package.manifest[field], pointer(field)):
                yield issue('package.json', path, reason)
        for key, resource in sorted(package.document('file.json')['items'].items()):
            if resource['kind'] in {'document', 'model'} and not resource.get('dependency'):
                yield issue('file.json', pointer('items', key, 'dependency'), 'Resource bytes not fingerprinted')
        if not package.available('model.json') and any(r['kind'] == 'model' for r in package.document('file.json')['items'].values()):
            yield issue('model.json', '', 'Discovered model resources have no collected configuration')
    for key, row in sorted(package.document('device.json')['items'].items()):
        if device and key != device:
            continue
        if category and (not isinstance(row['categories'], list) or category not in row['categories']):
            continue
        for field in ('categories', 'voltage', 'view'):
            for path, reason in unknowns(row[field], pointer('items', key, field)):
                yield issue('device.json', path, reason, key)
        if 'dir' not in row:
            yield issue('device.json', pointer('items', key, 'dir'), 'Device details not collected', key)
            continue
        for suffix in ('cdf', 'symbol', 'simulation'):
            name = row['dir'] + '/' + suffix + '.json'
            if not package.available(name):
                yield issue(name, '', 'Device facts not collected', key)
                continue
            value = package.document(name)
            if suffix == 'cdf':
                if value['presence'] == 'unknown':
                    yield issue(name, '/presence', 'CDF presence not established', key)
                inputs, selected = collection_parameters(value)
                if not selected and any(p['write'] == 'unknown' for p in value['parameters'].values()):
                    yield issue(name, '/parameters', 'Core input scope requires this PDK user manual, '
                                'actual CDF name mapping and user discussion', key)
                for parameter in sorted(selected):
                    definition = value['parameters'][parameter]
                    # Auxiliary fields are retained as observations, not a full
                    # enrichment backlog. Read dependencies need type/unit;
                    # defaults matter only when the declared strategy uses one.
                    fields = ('type', 'unit', 'meaning', 'default', 'domain') if parameter in inputs else (
                        ('type', 'unit', 'default') if definition['requirement'] in {'default', 'conditional'}
                        else ('type', 'unit'))
                    for field in fields:
                        for path, reason in unknowns(definition[field], pointer('parameters', parameter, field)):
                            yield issue(name, path, reason, key)
                if selected:
                    for path, reason in unknowns(value['execution'], '/execution'):
                        yield issue(name, path, reason, key)
            else:
                for path, reason in unknowns(value):
                    yield issue(name, path, reason, key)
                if suffix == 'simulation' and 'setup' not in value:
                    yield issue(name, '/setup', 'Simulation setup not collected', key)
