"""Gate contract tests: real topology invariants, profile rejection and transport."""
import json
from pathlib import Path

import pytest

from cadai.circuit_gate import GATE_TOOLS, preview_gpdk_gate, qualify_gpdk
from cadai.circuit_tools import CircuitArgumentError, build_circuit_skill
from cadai.mcp import McpServer
from test_circuit_tools import Client, call

ROOT = Path('/zone.disk/hdd3/cold-archive/Training.Materials/cadence/skillMaestro/mae_RAK/gpdk045_v_3_5')
SPEC = dict(library='gateWork', cell='nand', gate='NAND2', gpdk_root=str(ROOT))


def test_nand_series_bulk_and_interface_contract():
    p = preview_gpdk_gate(SPEC)
    devices = {d[0]: dict(d[6]) for d in p['dut']['devices']}
    assert devices['MN0'] == dict(G='A', S='NINT', D='Y', B='VSS')
    assert devices['MN1'] == dict(G='B', S='VSS', D='NINT', B='VSS')
    assert devices['MP0'] == dict(G='A', S='VDD', D='Y', B='VDD')
    assert devices['MP1'] == dict(G='B', S='VDD', D='Y', B='VDD')
    dut = {x[0]:x[1] for x in p['dut']['ports']}
    symbol = {x[0]:x[1] for x in p['dut']['symbol_ports']}
    assert dut == symbol == dict(VDD='inputOutput', VSS='inputOutput', A='input', B='input', Y='output')
    for _, direction, (x,y) in p['dut']['symbol_ports']:
        if direction == 'inputOutput': assert x < 0 and y > 0
        elif direction == 'input': assert x < 0 and y < 0
        else: assert x > 0
    assert dict(p['testbench']['devices'][0][6]) == dict(VDD='VDD', VSS='gnd!', A='A', Y='Y', B='B')
    assert p['testbench']['devices'][0][0] == 'I_nand_0'
    assert all(point[0] != 'DUT' for wire in p['testbench']['wires'] for point in wire[1:])
    assert p['targets'][-1] == ['gateWork', 'nand_tb', 'schematic']
    assert p['simulation_qualified'] is False


def test_inv_profile_digest_and_numeric_spec_are_fixed():
    p = preview_gpdk_gate({**SPEC, 'gate':'INV'})
    assert len(p['dut']['devices']) == 2
    assert p['dimensions_nm'] == dict(l=45, nmos_w=240, pmos_w=480)
    assert 'B' not in {x[0] for x in p['dut']['ports']}
    assert p['preview_digest'] != preview_gpdk_gate(SPEC)['preview_digest']
    for key, value in [('cell','other'),('gpdk_root','/another/pdk')]:
        assert preview_gpdk_gate({**SPEC,key:value})['preview_digest'] != preview_gpdk_gate(SPEC)['preview_digest']


@pytest.mark.parametrize('update', [dict(gate='NOR'),dict(library='gpdk045'),dict(library='basic'),
    dict(cell='a b'),dict(gpdk_root='relative'),dict(callback='exit()'),dict(width='1u'),dict(gate=True)])
def test_invalid_gate_contract(update):
    with pytest.raises(CircuitArgumentError): preview_gpdk_gate({**SPEC,**update})


def test_profile_missing_or_modified_file_rejected(tmp_path):
    with pytest.raises(CircuitArgumentError): qualify_gpdk(tmp_path)
    manifest = json.loads((Path(__file__).resolve().parents[2]/'reference/circuit-recipes/gpdk045_v3_5.json').read_text())
    first = next(iter(manifest['sha256']))
    (tmp_path/first).parent.mkdir(parents=True)
    (tmp_path/first).write_text('wrong version')
    with pytest.raises(CircuitArgumentError): qualify_gpdk(tmp_path)


def test_create_digest_native_transport_and_verification_isolation(monkeypatch):
    monkeypatch.setattr('cadai.circuit_gate.qualify_gpdk', lambda root: None)
    args = {**SPEC, 'task_ref':'task:one', 'request_id':'gate1',
            'preview_digest':preview_gpdk_gate(SPEC)['preview_digest']}
    code = build_circuit_skill('create_gpdk_gate', args)
    assert code.startswith('aiGateCreate(') and 'list("G" "A")' in code
    with pytest.raises(CircuitArgumentError, match='preview_digest'):
        build_circuit_skill('create_gpdk_gate', {**args,'gate':'INV'})
    client = Client(); server = McpServer(client)
    assert call(server,'preview_gpdk_gate',SPEC)['result']['isError'] is False
    assert not client.calls
    assert call(server,'create_gpdk_gate',args)['result']['isError'] is False
    assert client.calls == [('eval_skill_native',{'code':code})]


def test_gate_verification_profile_when_available():
    import inspect
    if 'profile' not in inspect.signature(McpServer).parameters:
        pytest.skip('verification profile extension is not installed')
    for definition in GATE_TOOLS:
        assert call(McpServer(Client(),profile='verification'),definition['name'],{})['error']['code'] == -32602


def test_tb_stimuli_left_loads_right_and_no_long_output_connection():
    for gate in ('INV', 'NAND2'):
        tb=preview_gpdk_gate({**SPEC, 'gate':gate})['testbench']
        for inst in tb['devices']:
            if inst[2] in ('vdc', 'vpulse'): assert inst[3][0] < 3
            if inst[2]=='cap': assert inst[3][0] > 4.25
        output_wires=[w for w in tb['wires'] if w[0]=='Y']
        assert len(output_wires)==2
        assert all(len(w)==3 and sum(isinstance(p[0],str) for p in w[1:])==1
                   for w in output_wires)
