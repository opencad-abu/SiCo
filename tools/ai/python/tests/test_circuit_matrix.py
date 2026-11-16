"""Matrix input policy and native transport contracts."""
import pytest
from cadai.circuit_tools import CircuitArgumentError, build_circuit_skill
from cadai.circuit_matrix import checked_matrix
from cadai.mcp import McpServer
from test_circuit_tools import Client, call

SPEC=dict(setup_ref='opamp:base',cell='matrix',request_id='m',gpdk_root='/rak/gpdk045_v_3_5')


def test_create_default_and_explicit_sweep_native_transport(monkeypatch):
    roots=[]
    monkeypatch.setattr('cadai.circuit_matrix.qualify_gpdk',lambda p: roots.append(str(p)))
    expected='aiMatrixCreate("opamp:base" "m" "matrix" "/rak/gpdk045_v_3_5" list(1e-13 2e-13) list(1000))'
    assert build_circuit_skill('create_opamp_matrix',SPEC)==expected
    client=Client()
    assert call(McpServer(client),'create_opamp_matrix',SPEC)['result']['isError'] is False
    assert client.calls==[('eval_skill_native',{'code':expected})]
    args={**SPEC,'cload_values_f':[2e-13],'rload_values_ohm':[1000,2000]}
    assert build_circuit_skill('create_opamp_matrix',args).endswith('list(2e-13) list(1000 2000))')
    assert len(roots)==3


@pytest.mark.parametrize('update',[
    {'cell':'bad/name'},{'gpdk_root':'relative'},{'cload_values_f':[]},
    {'cload_values_f':[True]},{'cload_values_f':[float('nan')]},
    {'cload_values_f':[1e-13]*2},{'cload_values_f':[1e-16]},
    {'cload_values_f':[1e-13,1.0000000000001e-13]},
    {'cload_values_f':[1e-13]*5},{'rload_values_ohm':[float('inf')]},
    {'rload_values_ohm':[99]},{'rload_values_ohm':['1000']},
    {'corner':['mc']},{'temperature':30},{'overwrite':True},{'setup_ref':None}])
def test_bad_matrix_arguments_rejected(monkeypatch,update):
    monkeypatch.setattr('cadai.circuit_matrix.qualify_gpdk',lambda p: None)
    with pytest.raises(CircuitArgumentError): build_circuit_skill('create_opamp_matrix',{**SPEC,**update})


def test_read_run_and_profile_checks(monkeypatch):
    for tool,fn,key in [('inspect_opamp_matrix','aiMatrixInspect','setup_ref'),
                        ('read_opamp_matrix_results','aiMatrixResults','run_ref')]:
        assert build_circuit_skill(tool,{key:'matrix:m'})==fn+'("matrix:m")'
    def reject(p): raise CircuitArgumentError('profile changed')
    monkeypatch.setattr('cadai.circuit_matrix.qualify_gpdk',reject)
    with pytest.raises(CircuitArgumentError,match='profile changed'):
        build_circuit_skill('run_opamp_matrix',dict(setup_ref='matrix:m',request_id='run',gpdk_root='/rak'))
    assert len(checked_matrix('create_opamp_matrix',SPEC)['cload_values_f'])==2
