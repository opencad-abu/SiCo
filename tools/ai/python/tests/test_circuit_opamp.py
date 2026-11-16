"""Reference-copy transport, input rejection and profile qualification."""
from pathlib import Path

import pytest

from cadai.circuit_opamp import OPAMP_TOOLS
from cadai.circuit_tools import CircuitArgumentError, build_circuit_skill
from cadai.mcp import McpServer
from test_circuit_tools import Client, call

SPEC = dict(library='work', cell='baseline', task_ref='task:fg', request_id='ref1', gpdk_root='/rak/gpdk045_v_3_5')


def test_copy_and_run_are_validated_native_calls(monkeypatch):
    roots=[]
    monkeypatch.setattr('cadai.circuit_opamp.qualify_gpdk',lambda root: roots.append(str(root)))
    monkeypatch.setattr(Path,'is_dir',lambda self: True)
    code=build_circuit_skill('create_opamp_reference_maestro',SPEC)
    assert code=='aiOpampCreate("task:fg" "ref1" "work" "baseline" "/rak/gpdk045_v_3_5" "/rak/libs/Two_Stage_Opamp")'
    client=Client()
    assert call(McpServer(client),'create_opamp_reference_maestro',SPEC)['result']['isError'] is False
    assert client.calls==[('eval_skill_native',{'code':code})]
    code=build_circuit_skill('run_opamp_reference',dict(setup_ref='opamp:ref1',request_id='r1',gpdk_root=SPEC['gpdk_root']))
    assert code=='aiOpampRun("opamp:ref1" "r1" "/rak/gpdk045_v_3_5")'
    assert roots==[SPEC['gpdk_root']]*3


@pytest.mark.parametrize('update',[
    {'library':'gpdk045'}, {'library':'Two_Stage_Opamp'}, {'cell':'../bad'},
    {'gpdk_root':'relative'}, {'overwrite':True}, {'task_ref':False}, {'request_id':'x)exit('},
])
def test_invalid_input_never_dispatches(monkeypatch,update):
    monkeypatch.setattr('cadai.circuit_opamp.qualify_gpdk',lambda root: None)
    with pytest.raises(CircuitArgumentError):
        build_circuit_skill('create_opamp_reference_maestro',{**SPEC,**update})


def test_profile_failure_and_missing_paired_reference(monkeypatch):
    def reject(root): raise CircuitArgumentError('profile changed')
    monkeypatch.setattr('cadai.circuit_opamp.qualify_gpdk',reject)
    with pytest.raises(CircuitArgumentError,match='profile changed'):
        build_circuit_skill('run_opamp_reference',dict(setup_ref='opamp:a',request_id='r',gpdk_root='/rak/gpdk'))
    monkeypatch.setattr('cadai.circuit_opamp.qualify_gpdk',lambda root: None)
    with pytest.raises(CircuitArgumentError,match='paired'):
        build_circuit_skill('create_opamp_reference_maestro',SPEC)


def test_inspect_status_result_and_release_contracts():
    names={'inspect_opamp_reference_maestro':'aiOpampInspect', 'get_opamp_reference_status':'aiOpampStatus',
           'read_opamp_reference_results':'aiOpampResults','stop_opamp_reference':'aiOpampStop',
           'release_opamp_reference':'aiOpampRelease'}
    for name,func in names.items():
        key='setup_ref' if name in ('inspect_opamp_reference_maestro','release_opamp_reference') else 'run_ref'
        assert build_circuit_skill(name,{key:'opamp:a'})==func+'("opamp:a")'


def test_opamp_verification_profile_when_available():
    import inspect
    if 'profile' not in inspect.signature(McpServer).parameters:
        pytest.skip('verification profile extension is not installed')
    for definition in OPAMP_TOOLS:
        assert call(McpServer(Client(),profile='verification'),definition['name'],{})['error']['code']==-32602
