"""A real binding service cannot override standard consent, bounds or revisions."""

from sicostate import project_directory

from copy import deepcopy

import pytest

from cadai.pdk_data import binding
from cadai.pdk_normalize import context, detail
from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_standard.package import publish
from cadai.pdk_standard.service import StandardData
from cadai.pdk_standard.workspace import select
from cadai.pdk_standard import gate
from pdk_binding_fixture import fixture, prepare_args
from pdk_standard_fixture import update, replace


def test_rules_without_a_spectre_interface_fail_closed_instead_of_stopping_iteration():
    interface = gate.spectre({'simulation': {'interfaces': {
        'spectre': {'simulator': 'spectre', 'terminal_map': {}, 'term_order': []}}}})
    assert interface['simulator'] == 'spectre'
    rule = {'cdf': {'parameters': {}, 'execution': {'mode': 'literal'}},
            'simulation': {'interfaces': {}}}
    with pytest.raises(PdkUnavailable) as exc:
        gate.contract(rule, {'writable_parameters': []})
    assert exc.value.code == 'pdk_policy_mismatch'
    assert 'spectre_interface_missing' in str(exc.value)


def test_anchor_mismatch_names_the_observed_and_expected_escape():
    rule = {'symbol': {'bbox': [[0, 0], [1, 1]], 'terminals': {
        'D': {'anchors': [{'id': 'pin0', 'xy': [0.5, 1.0], 'escape': 'up'}]},
        'S': {'anchors': [{'id': 'pin0', 'xy': [0.5, 0.0], 'escape': 'down'}]}}}}
    body = {'occupied_bbox': [[0, 0], [1, 1]]}
    pins = [
        {'name': 'D', 'anchors': [{'id': 'pin0', 'xy': [0.5, 1.0], 'escape': 'down'}]},
        {'name': 'S', 'anchors': [{'id': 'pin0', 'xy': [0.5, 0.0], 'escape': 'up'}]},
    ]
    with pytest.raises(PdkUnavailable) as exc:
        gate.geometry(rule, body, pins)
    assert exc.value.code == 'pdk_policy_mismatch'
    assert 'terminal D pin 0' in str(exc.value)
    assert "'escape': 'down'" in str(exc.value)
    assert "'escape': 'up'" in str(exc.value)


def test_binding_preparation_and_execute_require_effective_standard(tmp_path, monkeypatch):
    bridge, bindings, args = fixture()
    target = {'library': 'synthetic', 'cell': 'cell0', 'view': 'symbol'}
    captured = bridge.capture('device', target)
    device = detail(captured['data'], context(captured['context']))
    device['categories'] = {'all': ['test']}
    payload = {'binding': binding(context(bridge.ctx), 'synthetic'),
               'directory': bridge.capture('directory', {})['data']}
    publish(project_directory(tmp_path, "ai/pdk-data", create=True), 'synthetic', payload, {'cell0': device}, {})
    bindings.session.standard = StandardData(tmp_path, {})
    with pytest.raises(PdkUnavailable, match='generation incomplete'):
        bindings.bind(args)
    from pdk_publication_fixture import acceptance_evidence, ingest_simulation
    from cadai.pdk_standard.publication import publish as publish_ready
    ingest_simulation(tmp_path, select(tmp_path, 'synthetic', {}))
    changes = [
        ('device.json', [replace('/items/cell0/use/circuit', 'allow'),
                         replace('/items/cell0/voltage', {'state': 'not_applicable', 'reason': 'Synthetic'})]),
        ('cell0/cdf.json', [replace('/parameters/w/write', 'allow'),
                           replace('/parameters/w/requirement', 'explicit'),
                           replace('/parameters/w/meaning', 'Test width'),
                           replace('/parameters/w/domain', {'kind': 'interval', 'min': 1e-7, 'max': 2e-6}),
                           replace('/execution', {'mode': 'literal'})]),
        ('cell0/symbol.json', [replace('/bbox', [[-1, -1], [1, 1]]),
                              replace('/grid', {'step': [0.125, 0.125], 'origin': [0, 0]}),
                              replace('/terminals/A/anchors', [{'id': 'pin0', 'xy': [0, 0], 'escape': 'left'}])]),
    ]
    for file, ops in changes:
        updates, preview = update(tmp_path, select(tmp_path, 'synthetic', {}), ops, file)
        updates.apply({'update_ref': preview['update_ref']})
    current = select(tmp_path, 'synthetic', {})
    publish_ready(tmp_path, 'synthetic', current.revision, {},
                  acceptance=acceptance_evidence(tmp_path))
    public = bindings.bind(args)
    assert "standard_rule" not in public
    record = bindings.get(public["binding_ref"])
    assert record['standard_rule']['use'] == 'circuit'
    prepared = prepare_args(record)
    prepared['spec']['kind'] = 'circuit'
    assert bindings.validate_prepare(prepared)
    assert bindings.validate_execute([record['binding_ref']])
    invalid = deepcopy(args)
    invalid['writable_parameters'] = []
    with pytest.raises(PdkUnavailable, match='confirmed standard set') as failure:
        bindings.bind(invalid)
    assert "get_pdk_data(section='cdf')" in str(failure.value)
    # 廉价契约检查在实时明细读取之前失败：错误确认不得先付数分钟桥工作。
    from cadai import pdk_binding as binding_module

    def blocked(session, request):
        raise AssertionError("live detail read must not run for a wrong confirmation")

    original_reader = binding_module.read_device
    monkeypatch.setattr(binding_module, "read_device", blocked)
    try:
        with pytest.raises(PdkUnavailable, match='confirmed standard set'):
            bindings.bind(invalid)
    finally:
        monkeypatch.setattr(binding_module, "read_device", original_reader)
    verified = []
    original_verify = gate.verify
    monkeypatch.setattr(gate, "verify", lambda package: verified.append(package))
    try:
        with pytest.raises(PdkUnavailable, match='confirmed standard set'):
            bindings.bind(invalid)
    finally:
        monkeypatch.setattr(gate, "verify", original_verify)
    assert not verified
    with pytest.raises(PdkUnavailable, match='constraints'):
        gate.values(record['standard_rule'], {'w': '5u'})
    with pytest.raises(PdkUnavailable, match='Explicit'):
        gate.values(record['standard_rule'], {})
    changed, preview = update(tmp_path, select(tmp_path, 'synthetic', {}), [replace('/items/cell0/use/circuit', 'deny')])
    changed.apply({'update_ref': preview['update_ref']})
    with pytest.raises(PdkUnavailable, match='rules changed'):
        bindings.validate_execute([record['binding_ref']])

    changed, preview = update(tmp_path, select(tmp_path, 'synthetic', {}), [replace('/items/cell0/use/circuit', 'allow')])
    changed.apply({'update_ref': preview['update_ref']})
    current = select(tmp_path, 'synthetic', {})
    publish_ready(tmp_path, 'synthetic', current.revision, {},
                  acceptance=acceptance_evidence(tmp_path))
    rebound = bindings.bind(args)
    assert rebound['binding_ref'] != record['binding_ref']
    assert bindings.validate_execute([rebound['binding_ref']])

    # cdf 执行模式：callback_order 必须等于已发布 execution.order，失败必须指向可读来源。
    order_update, preview = update(
        tmp_path, select(tmp_path, 'synthetic', {}),
        [replace('/execution', {'mode': 'cdf', 'order': ['w'],
                                'implementation_ref': 'builtin:cdf-callbacks:v1'})],
        'cell0/cdf.json')
    order_update.apply({'update_ref': preview['update_ref']})
    current = select(tmp_path, 'synthetic', {})
    publish_ready(tmp_path, 'synthetic', current.revision, {},
                  acceptance=acceptance_evidence(tmp_path))
    with pytest.raises(PdkUnavailable, match='execution.order') as failure:
        bindings.bind({**args, 'callback_order': ['wrong']})
    assert "get_pdk_data(section='cdf')" in str(failure.value)
    ordered = bindings.bind({**args, 'callback_order': ['w']})
    assert bindings.validate_execute([ordered['binding_ref']])
