"""Small task projections with revision-bound cursors and explicit missing rules."""

import secrets

from . import VERSION
from .constraints import unknown
from .jsonio import LIMIT, encode, fail, fingerprint
from .patches import pointer
from .policy_modes import references


def cdf_missing(cdf):
    """Check the declared interface closure; legacy captures check all parameters."""
    from .interface import parameters
    selected = parameters(cdf)
    if cdf['execution']['mode'] == 'unknown':
        yield '/execution', 'confirmation_required'
    if cdf['presence'] == 'unknown':
        yield '/presence', 'missing_required'
    for name, parameter in cdf['parameters'].items():
        if name not in selected:
            continue
        for field in ('write', 'requirement'):
            if parameter[field] == 'unknown':
                yield pointer('parameters', name, field), 'confirmation_required'
        if parameter['write'] in {'allow', 'conditional'}:
            for field in ('type', 'unit', 'meaning'):
                if unknown(parameter[field]):
                    yield pointer('parameters', name, field), 'missing_required'
            if parameter['domain']['kind'] in {'unknown', 'not_applicable'}:
                yield pointer('parameters', name, 'domain'), 'missing_required'


def symbol_missing(symbol):
    """Use one geometry-gap definition for detail reads and missing queries."""
    for field in ('bbox', 'grid', 'parameterized'):
        if unknown(symbol[field]):
            yield pointer(field), 'missing_required'
    if symbol['parameterized'] is True and unknown(symbol.get('geometry_parameters')):
        yield '/geometry_parameters', 'missing_required'
    for name, terminal in symbol['terminals'].items():
        if unknown(terminal['anchors']):
            yield pointer('terminals', name, 'anchors'), 'missing_required'


def missing(package, key, row, purpose='circuit'):
    result = []
    def add(file, path, reason='missing_required'):
        result.append({'device': key, 'file': file, 'path': path, 'reason': reason,
                       'operation': purpose, 'action': 'pdk_generation_required'})
    if row['use'].get(purpose) == 'deny':
        return result
    if row['use'].get(purpose) == 'unknown':
        add('device.json', pointer('items', key, 'use', purpose), 'confirmation_required')
    if unknown(row['voltage']) and row['voltage']['state'] == 'unknown':
        add('device.json', pointer('items', key, 'voltage'))
    directory = row.get('dir')
    if not directory:
        add('device.json', pointer('items', key, 'dir'))
        return result
    file = directory + '/cdf.json'
    cdf = package.document(file)
    for path, reason in cdf_missing(cdf):
        add(file, path, reason)
    file = directory + '/symbol.json'
    if not package.available(file):
        add(file, '')
    else:
        symbol = package.document(file)
        for path, reason in symbol_missing(symbol):
            add(file, path, reason)
            if 'x_geometry_collection' not in symbol and path != '/grid':
                result[-1]['generation_action'] = 'collect_pdk_data'
    # Simulation is checked by its consumer, not a requirement for schematic browsing.
    return result


def content_rows(package, devices, args):
    section, key = args['section'], args.get('device')
    per_device = section in {'cdf', 'cdf_modes', 'symbol', 'simulation', 'properties', 'iv'}
    directory = devices[key].get('dir') if per_device else None
    suffix = 'cdf' if section == 'cdf_modes' else section
    name = directory + '/' + suffix + '.json' if directory else section + '.json'
    if (per_device and not directory) or not package.available(name):
        return [], 'not_available'
    value = package.document(name)
    if section == 'cdf_modes':
        from .policy_modes import rows
        return rows(value)
    fields = {'cdf': ('parameters', 'rules'), 'symbol': ('terminals',), 'simulation': ('interfaces',),
              'category': ('items',), 'file': ('items',), 'model': ('configs', 'corners'),
              'sources': ('items',), 'properties': ('items',), 'iv': ('runs',)}[section]
    meta = {k: v for k, v in value.items() if k not in {*fields, 'source', 'depends_on', 'evidence'}}
    rows = [{'file': name, 'metadata': meta}] if meta else []
    selected = None
    if section == 'cdf':
        selected = ({args['parameter']} if args.get('parameter') else
                    set(value['parameters']) if args.get('include_unconfirmed') else
                    {n for n, p in value['parameters'].items() if p['write'] in {'allow', 'conditional'}
                     or p['requirement'] in {'explicit', 'conditional'}})
        if selected - set(value['parameters']):
            fail('Unknown CDF parameter')
        if 'interface_parameters' in value and not args.get('parameter'):
            from .interface import parameters
            selected |= parameters(value)
        selected |= references(value.get('rules', {}))
        while True:
            closure = selected | {dep for n in selected for dep in value['parameters'][n].get('depends_on', [])}
            closure |= references({n: value['parameters'][n] for n in selected})
            if closure == selected:
                break
            selected = closure
    for field in fields:
        for ident, row in sorted(value.get(field, {}).items()):
            if field == 'parameters' and selected is not None and ident not in selected:
                continue
            if section == 'iv':
                row = {k: v for k, v in row.items() if k not in {'data', 'data_index'}}
            rows.append({'file': name, 'section': field, 'id': ident, **row})
    incomplete = section == 'cdf' and next(cdf_missing(value), None)
    incomplete = incomplete or section == 'symbol' and next(symbol_missing(value), None)
    return rows, 'incomplete' if incomplete else 'ok'


class Queries:
    def __init__(self):
        self.cursors = {}

    def read(self, package, args):
        args = {'section': 'devices', 'page_size': 20, 'use': 'circuit', **args}
        query = {k: v for k, v in args.items() if k != 'cursor'}
        revision, offset = package.revision, 0
        if args.get('cursor'):
            claim = self.cursors.get(args['cursor'])
            if claim is None or claim[:2] != (revision, fingerprint(query)):
                fail('Query or effective revision changed', 'cursor_mismatch')
            offset = claim[2]
        from .publication import require
        publication_error = None
        from ..pdk_errors import PdkUnavailable
        try:
            require(package)
        except PdkUnavailable as exc:
            if exc.code != 'pdk_generation_incomplete':
                raise
            publication_error = exc.code
        devices = package.document('device.json')['items']
        key, section = args.get('device'), args['section']
        rows, status = [], 'ok'
        if key and key not in devices:
            fail('Unknown standard device ID', 'device_unavailable')
        if section == 'collection':
            from .coverage import gaps
            rows = list(gaps(package, key, args.get('category')))
            status = 'incomplete' if rows else 'ok'
        elif section in {'devices', 'missing'}:
            for ident, row in sorted(devices.items()):
                if key and ident != key:
                    continue
                if args.get('category') and args['category'] not in row.get('categories', []):
                    continue
                if section == 'missing':
                    if row['use'][args['use']] == 'allow':
                        from .readiness import device_issues
                        rows.extend(device_issues(package, ident, row, args['use']))
                    else:
                        rows.extend(missing(package, ident, row, args['use']))
                elif args.get('include_unconfirmed') or (not publication_error and row['use'][args['use']] == 'allow'):
                    rows.append({'id': ident, **row})
            if section == 'missing' and rows:
                status = 'incomplete'
        elif section == 'package':
            rows = [{k: v for k, v in package.manifest.items() if k not in {'files', 'dependencies', 'libraries'}}]
            rows.extend({'library_id': k, **v} for k, v in package.manifest['libraries'].items())
        else:
            rows, status = content_rows(package, devices, args)
        response = {'schema_version': VERSION, 'revision': revision, 'status': status,
                    'items': [], 'returned': 0, 'complete': False}
        if section == 'devices' and not rows and any(row['use'][args['use']] == 'unknown' for row in devices.values()):
            response['status'] = 'incomplete'
            response['missing'] = [{'file': 'device.json', 'reason': 'confirmation_required',
                                    'operation': args['use'], 'action': 'pdk_generation_required',
                                    'details_ref': 'get_pdk_data:section=missing'}]
        response['design_ready'] = publication_error is None
        if publication_error:
            response.update(code=publication_error, next_action='complete_pdk_generation')
            if section == 'devices' and not args.get('include_unconfirmed'):
                response['status'] = 'incomplete'
        for row in rows[offset:offset + args['page_size']]:
            if len(encode({**response, 'items': response['items'] + [row]})) > LIMIT - 1024:
                break
            response['items'].append(row)
        if offset < len(rows) and not response['items']:
            fail('One detail exceeds response budget; narrow the query', 'pdk_data_limit')
        end = offset + len(response['items'])
        response.update(returned=len(response['items']), complete=end == len(rows), total=len(rows))
        if end < len(rows):
            cursor = 'pdk-page:' + secrets.token_urlsafe(18)
            if len(self.cursors) >= 256:
                del self.cursors[next(iter(self.cursors))]
            self.cursors[cursor] = (revision, fingerprint(query), end)
            response['next_cursor'] = cursor
        return response
