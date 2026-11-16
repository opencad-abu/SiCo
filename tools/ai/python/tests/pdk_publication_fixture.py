"""Synthetic generation fixtures: factual ingestion precedes real review mechanics."""

from sicostate import project_directory

from cadai.pdk_standard.ingest import publish
from cadai.pdk_standard.workspace import select


def simulation(workspace, current):
    interface = {'simulator': 'spectre', 'kind': 'primitive', 'name': 'test_element',
                 'model_source': 'builtin', 'term_order': ['A'], 'terminal_map': {'A': 'A'},
                 'parameters': {'w': {'cdf': 'w'}}}
    return {'source': 'capture', 'depends_on': ['cell0_interface'],
            'interfaces': {'spectre': interface},
            'setup': {'switch_views': ['spectre', 'schematic'], 'stop_views': ['spectre'], 'options': {}}}


def two_device_candidate(workspace, second='l', second_prompt='Second length',
                         classify_second=True):
    """One package with two devices whose CDF name and role are chosen per case."""
    from pdk_fixture import Bridge, raw

    from cadai.pdk_data import binding
    from cadai.pdk_normalize import context, detail
    from cadai.pdk_standard.package import publish as capture

    def observe(bridge, cell):
        captured = bridge.capture('device', {'library': 'synthetic', 'cell': cell, 'view': 'symbol'})
        return detail(captured['data'], context(captured['context']))

    bridge = Bridge()
    bridge.count = 2
    template = bridge.data['cdf']['parameters']['items'][0]
    template.update(defValue=raw('0.3'), callback=raw(None), prompt=raw('Test width'))
    first = observe(bridge, 'cell0')
    first['categories'] = {'all': ['test']}
    template['name'] = raw(second)
    template['prompt'] = raw(second_prompt)
    second_obs = observe(bridge, 'cell1')
    if classify_second:
        second_obs['categories'] = {'all': ['test']}
    directory = bridge.capture('directory', {})['data']
    names = {'cell0': ['w'], 'cell1': [second]}
    for item in directory['items']:
        item['cdf_summary']['names'] = names[item['identity']['target']['cell']]
    payload = {'binding': binding(context(bridge.ctx), 'synthetic'), 'directory': directory}
    capture(project_directory(workspace, 'ai/pdk-data', create=True), 'synthetic', payload,
            {'cell0': first, 'cell1': second_obs}, {})
    return select(workspace, 'synthetic', {})


def acceptance_evidence(workspace, devices=('cell0',), *, operations=None,
                        verifier=None, name='acceptance-evidence.json', environment=None):
    """One recorded device-acceptance run covering the given devices.

    Synthetic evidence for the publication gate; the real verifier writes the
    same shape from its recorded runs.
    """
    import json
    from pathlib import Path

    from cadai.pdk_standard.receipts import OPERATIONS, SCHEMA, VERIFIER
    from cadai.pdk_standard.acceptance_run import begin, finish

    target = Path(workspace) / name
    target.parent.mkdir(parents=True, exist_ok=True)
    package = select(workspace, 'synthetic', environment or {})
    operations = list(operations if operations is not None else OPERATIONS)
    # Synthetic gate fixtures do not claim real Virtuoso operation acceptance.
    validate = lambda package: None
    snapshot = begin(package, list(devices), validate)
    run = finish(package, snapshot,
                 {device: {operation: 'passed' for operation in operations} for device in devices},
                 at='2026-10-14T00:00:00Z', run_ref='acceptance:fixture',
                 source_validator=validate, evidence_ref='acceptance:fixture', operations=operations)
    target.write_text(json.dumps({
        'schema_version': SCHEMA,
        'verifier': verifier or VERIFIER,
        'runs': [run]}))
    return str(target)


def ingest_simulation(workspace, current, *, dependencies=None):
    publish(project_directory(workspace, 'ai/pdk-data', create=True), current.base,
            {'cell0/simulation.json': simulation(workspace, current)}, dependencies=dependencies)
    return select(workspace, 'synthetic', {})


def ingest_interface_free(workspace, current, *, dependencies=None):
    """The marker-style shape: no interfaces, recorded run evidence only."""
    sources = current.base.document('sources.json')
    sources['items']['probe_01'] = {
        'kind': 'simulation', 'at': '2026-10-11T00:00:00Z',
        'summary': 'Recorded simulator-builtin netlist probe', 'run_ref': 'acceptance:test',
        'simulator': 'spectre 23.10', 'evidence_ref': 'acceptance:test'}
    document = {'source': 'capture', 'depends_on': ['cell0_interface'], 'interfaces': {},
                'evidence': {'/interfaces': 'probe_01'}}
    publish(project_directory(workspace, 'ai/pdk-data', create=True), current.base,
            {'cell0/simulation.json': document, 'sources.json': sources},
            dependencies=dependencies)
    return select(workspace, 'synthetic', {})


def ready_candidate(workspace, *, auxiliary=0, named=(), dependency_count=0,
                    interface_free=False):
    workspace.mkdir(parents=True, exist_ok=True)
    from copy import deepcopy

    from pdk_binding_fixture import fixture
    from pdk_fixture import raw
    from pdk_standard_fixture import replace, update

    from cadai.pdk_data import binding
    from cadai.pdk_normalize import context, detail
    from cadai.pdk_standard.package import publish as capture
    bridge, _, _ = fixture()
    bridge.ctx['libraries'][0]['technology_library'] = 'synthetic'
    bridge.ctx['libraries'][0]['resolved_path'] = str(workspace / 'pdk')
    for number in range(auxiliary):
        row = deepcopy(bridge.data['cdf']['parameters']['items'][0])
        row['name'] = raw('aux' + str(number))
        bridge.data['cdf']['parameters']['items'].append(row)
    for name in named:
        row = deepcopy(bridge.data['cdf']['parameters']['items'][0])
        row['name'] = raw(name)
        bridge.data['cdf']['parameters']['items'].append(row)
    observation = bridge.capture('device', {'library': 'synthetic', 'cell': 'cell0', 'view': 'symbol'})
    device = detail(observation['data'], context(observation['context']))
    device['categories'] = {'all': ['test']}
    payload = {'binding': binding(context(bridge.ctx), 'synthetic'),
               'directory': bridge.capture('directory', {})['data']}
    capture(project_directory(workspace, 'ai/pdk-data', create=True), 'synthetic', payload, {'cell0': device}, {})
    current = select(workspace, 'synthetic', {})
    dependencies = {'extra%04d' % i: deepcopy(current.manifest['dependencies']['cell0_cdf'])
                    for i in range(dependency_count)}
    if interface_free:
        ingest_interface_free(workspace, current, dependencies=dependencies)
    else:
        ingest_simulation(workspace, current, dependencies=dependencies)
    edits = [
        ('device.json', [replace('/items/cell0/use/circuit', 'allow'),
                         replace('/items/cell0/voltage', {'state': 'not_applicable', 'reason': 'Synthetic'})]),
        ('cell0/cdf.json', [replace('/parameters/w/write', 'allow'),
                           replace('/parameters/w/requirement', 'explicit'),
                           replace('/parameters/w/meaning', 'Width'),
                           replace('/parameters/w/domain', {'kind': 'interval', 'min': 1e-7, 'max': 2e-6}),
                           replace('/execution', {'mode': 'literal'}),
                           {'op': 'add', 'path': '/interface_parameters', 'value': ['w']}]),
        ('cell0/symbol.json', [replace('/bbox', [[-1, -1], [1, 1]]),
                              replace('/grid', {'step': [0.125, 0.125], 'origin': [0, 0]}),
                              replace('/terminals/A/anchors', [{'id': 'pin0', 'xy': [0, 0], 'escape': 'left'}])])]
    for file, operations in edits:
        current = select(workspace, 'synthetic', {})
        updates, preview = update(workspace, current, operations, file)
        updates.apply({'update_ref': preview['update_ref']})
    return select(workspace, 'synthetic', {}), bridge, device
