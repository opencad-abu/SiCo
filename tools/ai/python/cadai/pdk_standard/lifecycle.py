"""Attach standard readiness to existing resumable capture lifecycle results."""

from sicostate import project_directory
from ..pdk_errors import PdkUnavailable
from .workspace import select


def status(workspace, library, environment):
    if not workspace:
        return {'status': 'unavailable', 'design_ready': False, 'reason': 'workspace_required'}
    try:
        package = select(workspace, library, environment)
    except PdkUnavailable as exc:
        return {'status': 'legacy' if exc.code == 'pdk_standard_unavailable' else 'conflict',
                'design_ready': False, 'reason': exc.code, 'next_action': 'prepare_pdk_data_refresh' if exc.code == 'pdk_standard_unavailable' else 'resolve_conflict'}
    from .publication import require, PROFILE
    from .provenance import verify
    try:
        verify(package)
        require(package)
        published = True
    except PdkUnavailable as exc:
        if exc.code not in {'pdk_generation_incomplete', 'pdk_acceptance_required'}:
            return {'status': 'conflict', 'design_ready': False, 'reason': exc.code}
        published = False
    from .acceptance_state import collection_complete
    ready_for_acceptance = not published and collection_complete(package)
    next_action = 'get_pdk_data' if published else (
        'accept_pdk_data' if ready_for_acceptance else None)
    if next_action is None:
        from .blockers import report as blocker_report

        next_action = blocker_report(package)['next_action']
    return {'schema_version': package.manifest['schema_version'], 'status': 'published' if published else 'capture_ready',
            'revision': package.revision,
            'path': str((package.overlay or package.base.root) / 'package.json'),
            'design_ready': published,
            'readiness_scope': PROFILE if published else 'metadata_capture_only',
            'acceptance_required': ready_for_acceptance,
            'next_action': next_action}


def attach(prepared, data, library):
    prepared['standard'] = status(data.workspace, library, data.environment)
    if data.workspace and prepared['source'] == 'builtin':
        state = prepared['standard']['status']
    else:
        state = None
    if state in {'legacy', 'capture_ready', 'published'}:
        from pathlib import Path
        from ..pdk_normalize import context
        ctx = context(prepared['capture']['context'])
        lib = next(row for row in ctx['libraries'] if row['name'] == library)
        root = project_directory(data.workspace, "ai/pdk-data", create=True)
        roots = {'pdk': str(Path(lib['resolved_path']).parent)}
        if state == 'published':
            # A moved installation re-points the same bundled baseline; content never changes.
            data.builtin.rebind(root, library, roots)
        else:
            # Fresh install, or an unconfirmed projection replaced by the published bundle.
            data.builtin.install(root, library, roots)
        prepared['standard'] = status(data.workspace, library, data.environment)
    return prepared


def reuse(data, library, values):
    """An existing standard baseline owns the rules; fact cache refresh cannot replace it."""
    from .source_check import observed
    try:
        package = select(data.workspace, library, data.environment)
    except PdkUnavailable as exc:
        if exc.code == 'pdk_standard_unavailable':
            return None
        raise
    rows = package.document('device.json')['items']
    for key, row in rows.items():
        if 'dir' not in row:
            continue
        if row['cell'] not in values:
            raise PdkUnavailable('pdk_source_changed', 'Existing baseline cell is missing from fresh capture')
        refs = set()
        for suffix in ('cdf.json', 'symbol.json', 'simulation.json'):
            name = row['dir'] + '/' + suffix
            if package.available(name):
                refs.update(package.document(name)['depends_on'])
        observed(package, key, values[row['cell']], refs)
    known = {row['cell'] for row in rows.values()}
    if set(values) - known:
        raise PdkUnavailable('pdk_source_changed', 'Fresh capture adds cells; review/rebase required')
    return status(data.workspace, library, data.environment)
