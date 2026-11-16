"""Synthetic standard package with many parameters, independent of a PDK installation."""

from sicostate import project_directory

from copy import deepcopy

from cadai.pdk_normalize import context, detail
from cadai.pdk_data import binding
from cadai.pdk_standard.package import publish
from cadai.pdk_standard.workspace import select
from cadai.pdk_standard.updates import Updates
from pdk_fixture import Bridge, raw


def package(workspace, count=1):
    workspace.mkdir(parents=True, exist_ok=True)
    bridge = Bridge()
    bridge.count = 1
    bridge.ctx['libraries'][0]['resolved_path'] = str(workspace / 'pdk')
    parameters = bridge.data['cdf']['parameters']['items']
    template = deepcopy(parameters[0])
    template.update(defValue=raw('0.3'), callback=raw(None), prompt=raw('Test width'))
    parameters[:] = [{**deepcopy(template), 'name': raw('w' if i == 0 else 'p' + str(i))} for i in range(count)]
    target = {'library': 'synthetic', 'cell': 'cell0', 'view': 'symbol'}
    captured = bridge.capture('device', target)
    observed = detail(captured['data'], context(captured['context']))
    observed['categories'] = {'all': ['test']}
    directory = bridge.capture('directory', {})['data']
    directory['items'][0]['cdf_summary']['names'] = list(p['name']['value'] for p in parameters)
    payload = {'binding': binding(context(bridge.ctx), 'synthetic'), 'directory': directory}
    result = publish(project_directory(workspace, "ai/pdk-data", create=True), 'synthetic', payload, {'cell0': observed}, {})
    return select(workspace, 'synthetic', {}), bridge, observed, result


def update(workspace, effective, operations, file='device.json', host=True):
    updates = Updates(workspace, {})
    if host:
        updates.confirmation = lambda preview: {'decision': 'confirm', 'actor': 'test user', 'evidence_ref': 'audit:test'}
    preview = updates.prepare({'library': 'synthetic', 'revision': effective.revision,
                               'changes': [{'file': file, 'ops': operations}]})
    return updates, preview


def replace(path, value):
    return {'op': 'replace', 'path': path, 'value': value}
