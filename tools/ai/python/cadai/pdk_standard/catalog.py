"""Read a published standard directory through the legacy session binding adapter."""

from copy import deepcopy
from pathlib import Path

from ..pdk_errors import PdkUnavailable
from ..pdk_normalize import context
from .jsonio import fail
from .publication import require
from .provenance import verify
from .workspace import select


def prepared(data, library, raw_context):
    if not data.workspace:
        return None
    try:
        package = select(data.workspace, library, data.environment)
        require(package)
    except PdkUnavailable as exc:
        if exc.code in {'pdk_standard_unavailable', 'pdk_generation_incomplete'}:
            return None
        raise
    verify(package)
    live = next(row for row in context(raw_context)['libraries'] if row['name'] == library)
    items = []
    for row in package.document('device.json')['items'].values():
        lib = package.manifest['libraries'][row['library']]
        if lib['name'] != library:
            continue
        root = package.roots.get(lib['root'])
        if not root or (Path(root) / lib['path']).resolve() != Path(live['resolved_path']).resolve():
            fail('Published PDK library differs from live session', 'pdk_source_changed')
        if not any(row['use'][p] == 'allow' for p in ('circuit', 'testbench')):
            continue
        cdf = package.document(row['dir'] + '/cdf.json')
        items.append({'identity': {
            'target': {'library': library, 'cell': row['cell'], 'view': row['view']},
            'source': 'published_standard', 'available_views': [row['view']]},
            'cdf_summary': {'status': 'complete', 'presence': cdf['presence'],
                            'names': list(cdf['parameters'])}})
    from ..pdk_data import binding
    from .lifecycle import status
    return {'binding': binding(context(raw_context), library), 'source': 'published_standard',
            'capture': {'context': deepcopy(raw_context), 'data': {
                'items': items, 'status': 'complete', 'truncated': False, 'issues': []}},
            'path': str(package.base.root / 'package.json'), 'digest': package.revision,
            'standard': status(data.workspace, library, data.environment)}
