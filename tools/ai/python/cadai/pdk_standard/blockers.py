"""Turn a PDK package's findings into prioritized, actionable root causes.

The detailed missing/collection projections are intentionally exhaustive and
can contain thousands of paths.  A blocked workflow needs the opposite: a
small ordered list that says what the upstream cause is, whether SiCo can
repair it automatically, and which tool the caller should use next.
"""

from collections import Counter

from . import VERSION

MAX_ROOTS = 8
MAX_EXAMPLES = 5


def _examples(rows, key='device'):
    result = []
    for row in rows:
        value = row.get(key) or row.get('file') or row.get('path')
        if value and value not in result:
            result.append(str(value))
        if len(result) == MAX_EXAMPLES:
            break
    return result


def _root(code, title, detail, next_action, tool, *, auto=False, count=0, examples=(), findings=0):
    return {'code': code, 'title': title, 'detail': detail,
            'next_action': next_action, 'tool': tool, 'automatic': auto,
            'count': count, 'examples': list(examples), 'findings': findings}


def report(package):
    """Return a bounded root-cause report for the effective package."""
    from .coverage import gaps
    from .readiness import issues

    rows = package.document('device.json')['items']
    files = package.document('file.json')['items']
    collection = list(gaps(package))
    readiness = list(issues(package))
    model_files = {key: row for key, row in files.items() if row.get('kind') == 'model'}
    allowed = {key for key, row in rows.items()
               if any(row['use'].get(purpose) == 'allow' for purpose in ('circuit', 'testbench'))}
    non_allowed = set(rows) - allowed
    missing_simulation = [key for key in sorted(allowed)
                          if rows[key].get('dir')
                          and not package.available(rows[key]['dir'] + '/simulation.json')]
    unbound = [key for key, row in model_files.items() if not row.get('dependency')]
    model_missing = bool(model_files) and not package.available('model.json')
    model_blocked = []
    if model_missing and getattr(package, 'roots', None):
        try:
            from .model_collection import derive_package

            _, _, _, model_issues = derive_package(package, 'model_deck_derivation')
            model_blocked = [issue for issue in model_issues if issue.get('code') in {
                'model_configuration_ambiguous', 'model_default_corner_missing',
                'model_configuration_section_missing',
                'model_resource_unavailable', 'model_resource_path_missing',
                'model_simulator_unknown', 'model_resource_budget_exceeded'}]
        except (KeyError, OSError, ValueError):
            model_blocked = [{'message': 'model deck structure could not be read'}]
    blocked, full = [], []

    if model_missing:
        detail = (f'{len(model_files)} model resources are registered, but model.json is '
                  'absent. collect_pdk_data now derives the configuration from the declared '
                  'deck sections and fingerprints every model file.')
        if model_blocked:
            detail += ' Blocking deck issue(s): ' + '; '.join(
                str(issue.get('message', issue)) for issue in model_blocked[:3]) + '.'
        blocked.append(_root(
            'model_configuration_missing',
            'Registered model decks have no model configuration',
            detail, 'collect_pdk_data', 'collect_pdk_data', auto=not model_blocked,
            count=len(model_files), examples=sorted(model_files)[:MAX_EXAMPLES],
            findings=sum(1 for row in collection if row.get('file') == 'model.json')))
    elif unbound:
        blocked.append(_root(
            'model_dependency_missing',
            'Registered model decks have no byte dependency',
            f'{len(unbound)} model resources are not content-fingerprinted. '
            'collect_pdk_data will bind their sha256 bytes before interface collection.',
            'collect_pdk_data', 'collect_pdk_data', auto=True,
            count=len(unbound), examples=unbound[:MAX_EXAMPLES],
            findings=len(unbound)))

    if not allowed:
        blocked.append(_root(
            'no_usable_device',
            'No device is allowed for design',
            f'The package has {len(rows)} observed devices, but none is allowed for circuit or '
            'testbench use. At least one explicit user-confirmed usage decision is '
            'required before publication.',
            'confirm_device_usage', 'prepare_pdk_data_update',
            count=len(rows), examples=sorted(rows)[:MAX_EXAMPLES],
            findings=len(rows)))

    if missing_simulation:
        blocked.append(_root(
            'simulation_interface_missing',
            'Simulation interfaces have not been collected',
            f'{len(missing_simulation)} allowed device(s) have no simulation.json. '
            'call collect_pdk_data for each '
            'allowed device; the response names any device-specific blocker (CDF simInfo, '
            'terminal role, netlist identity or deck declaration).',
            'collect_pdk_data', 'collect_pdk_data', auto=True,
            count=len(missing_simulation), examples=missing_simulation[:MAX_EXAMPLES],
            findings=len(missing_simulation)))

    setup_missing = [row for row in readiness
                     if row.get('reason') == 'simulation_setup_missing']
    if setup_missing:
        blocked.append(_root(
            'simulation_setup_missing',
            'Simulation setup is not recorded',
            f'{len(setup_missing)} allowed device(s) have an interface but no setup '
            '(switch/stop views, '
            'options). Pass a recorded simulator probe as builtin_evidence to '
            'collect_pdk_data; its setup is copied into the simulation document.',
            'collect_pdk_data', 'collect_pdk_data', auto=True,
            count=len(setup_missing), examples=_examples(setup_missing),
            findings=len(setup_missing)))
    readiness_other = [row for row in readiness
                       if row.get('reason') not in {
                           'simulation_interface_missing', 'simulation_setup_missing'}]
    if readiness_other:
        reasons = Counter(row.get('reason') for row in readiness_other)
        blocked.append(_root(
            'readiness_gaps',
            'Allowed devices still fail design readiness',
            f'{len(readiness_other)} static readiness findings remain ('
            + ', '.join(f'{reason}={count}' for reason, count in reasons.most_common(4)) + ').',
            'inspect_missing', 'get_pdk_data',
            count=len({row.get('device') for row in readiness_other if row.get('device')}),
            examples=_examples(readiness_other), findings=len(readiness_other)))

    if not blocked and not package.manifest.get('publication'):
        blocked.append(_root(
            'acceptance_pending',
            'The allowed device set has not been published',
            'Run the real-device acceptance for the allowed devices, then publish this '
            'exact revision. Full-library devices outside the allowed set do not block it.',
            'accept_pdk_data', 'publish_pdk_data', count=len(allowed),
            examples=sorted(allowed)[:MAX_EXAMPLES], findings=len(allowed)))

    if non_allowed:
        full.append(_root(
            'usage_unconfirmed',
            'Devices outside the publishable scope are unconfirmed',
            f'{len(non_allowed)} devices are not allowed for circuit or testbench use. '
            'They remain '
            'observable, but do not block publishing the current allowed set; confirm '
            'or deny them when the full library is wanted.',
            'confirm_device_usage', 'prepare_pdk_data_update',
            count=len(non_allowed), examples=sorted(non_allowed)[:MAX_EXAMPLES],
            findings=len(non_allowed)))
    full_collection = [row for row in collection
                       if row.get('device') in non_allowed or row.get('device') is None]
    cdf_scope = [row for row in full_collection
                 if 'Core input scope requires' in row.get('reason', '')]
    grid = [row for row in full_collection
            if row.get('reason') == 'Symbol connection grid not captured']
    if cdf_scope:
        full.append(_root(
            'cdf_scope_unconfirmed',
            'CDF core-input scope is not confirmed',
            f'{len(cdf_scope)} devices outside the publishable scope have no confirmed '
            'core input set. '
            'Use the PDK user manual and '
            'actual CDF names to collect parameter facts, or explicitly deny devices '
            'that are not intended for design.',
            'collect_parameter_facts', 'collect_pdk_parameter_facts',
            count=len({row.get('device') for row in cdf_scope}),
            examples=_examples(cdf_scope),
            findings=len(cdf_scope)))

    if grid:
        full.append(_root(
            'symbol_grid_missing',
            'Symbol connection grid is not captured',
            f'{len(grid)} devices outside the publishable scope have no confirmed '
            'connection grid. '
            'The grid is a design fact; '
            'collect it from the PDK documentation or confirm it in a reviewed update.',
            'confirm_symbol_grid', 'prepare_pdk_data_update',
            count=len({row.get('device') for row in grid}),
            examples=_examples(grid), findings=len(grid)))

    other_collection = [row for row in full_collection
                        if row not in cdf_scope and row not in grid
                        and row.get('file') != 'model.json'
                        and row.get('reason') not in {
                            'Simulation interface has not been collected',
                            'Model resource bytes not fingerprinted'}]
    if other_collection:
        reasons = Counter(row.get('reason') for row in other_collection)
        full.append(_root(
            'other_collection_gaps',
            'Other facts for devices outside the publishable scope are incomplete',
            f'{len(other_collection)} findings remain in {len(reasons)} reason groups ('
            + ', '.join(f'{reason}={count}' for reason, count in reasons.most_common(4))
            + '). These do not block the current allowed set; inspect section=collection '
            'for exact paths.',
            'inspect_collection', 'get_pdk_data',
            count=len(other_collection), examples=_examples(other_collection),
            findings=len(other_collection)))

    roots = (blocked + full)[:MAX_ROOTS]
    next_action = (blocked[0]['next_action'] if blocked else
                   'get_pdk_data' if package.manifest.get('publication') else 'accept_pdk_data')
    return {
        'schema_version': VERSION,
        'status': 'ok' if not blocked and package.manifest.get('publication') else 'incomplete',
        'design_ready': not blocked and bool(package.manifest.get('publication')),
        'revision': package.revision,
        'totals': {'devices': len(rows), 'allowed_devices': len(allowed),
                   'collection_findings': len(collection),
                   'readiness_findings': len(readiness),
                   'blocking_root_causes': len(blocked),
                   'blocking_findings': sum(row['findings'] for row in blocked),
                   'full_library_root_causes': len(full),
                   'root_causes': len(roots)},
        'roots': roots,
        'publication_blockers': blocked,
        'full_library_gaps': full,
        'next_action': next_action,
    }


def message(package):
    """A concise failure message that is safe to return from a tool error."""
    data = report(package)
    if data['design_ready']:
        if data['full_library_gaps']:
            return (f'Published PDK is ready for the allowed device set; '
                    f'{len(data["full_library_gaps"])} full-library '
                    'gap group(s) remain outside that scope.')
        return 'PDK generation is complete and published.'
    lines = [f'Publication is blocked by {len(data["publication_blockers"])} root cause(s), '
             f'{data["totals"]["blocking_findings"]} blocking finding(s); '
             f'{data["totals"]["collection_findings"]} collection finding(s) total.']
    for index, root in enumerate(data['publication_blockers'][:MAX_ROOTS], 1):
        lines.append(f'{index}. {root["code"]}: {root["detail"]} '
                     f'Next: {root["next_action"]}.')
    if data['full_library_gaps']:
        lines.append(f'Full-library completion also has '
                     f'{len(data["full_library_gaps"])} gap group(s); these do not block '
                     'the current allowed set.')
    lines.append('Run get_pdk_data(section=blockers) for the bounded root-cause report.')
    return '\n'.join(lines)
