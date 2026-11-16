"""OpAmp rebuild transport and strict input contracts."""
from pathlib import Path
import pytest
from cadai.circuit_rebuild import REBUILD_TOOLS
from cadai.circuit_tools import CircuitArgumentError, build_circuit_skill
from cadai.mcp import McpServer
from test_circuit_tools import Client, call

SPEC = dict(library='work', cell='rebuilt', task_ref='task:fg', request_id='r1',
            gpdk_root='/rak/gpdk045_v_3_5')


def test_rebuild_transport_and_qualification(monkeypatch):
    roots=[]
    monkeypatch.setattr('cadai.circuit_rebuild.qualify_gpdk',lambda root: roots.append(str(root)))
    monkeypatch.setattr(Path,'is_dir',lambda path: True)
    cases=[
        ('preview_opamp_rebuild',SPEC,'aiRebuildPreview("task:fg" "r1" "work" "rebuilt" "/rak/gpdk045_v_3_5" "/rak/libs/Two_Stage_Opamp")'),
        ('create_opamp_dut',dict(preview_ref='rebuild-preview:r1',request_id='r2',gpdk_root=SPEC['gpdk_root']),
         'aiRebuildCreate("rebuild-preview:r1" "r2" "/rak/gpdk045_v_3_5")'),
        ('create_opamp_rebuild_maestro',dict(circuit_ref='rebuild:r2',request_id='m',gpdk_root=SPEC['gpdk_root']),
         'aiRebuildMaestro("rebuild:r2" "m" "/rak/gpdk045_v_3_5")'),
        ('run_opamp_rebuild',dict(setup_ref='opamp:rebuild_m',request_id='run',gpdk_root=SPEC['gpdk_root']),
         'aiRebuildRun("opamp:rebuild_m" "run" "/rak/gpdk045_v_3_5")'),
        ('inspect_opamp_dut',dict(circuit_ref='rebuild:r2'),'aiRebuildInspect("rebuild:r2")')]
    for name,args,expected in cases:
        assert build_circuit_skill(name,args)==expected
        client=Client()
        assert not call(McpServer(client),name,args)['result']['isError']
        assert client.calls==[('eval_skill_native',{'code':expected})]
    assert roots==[SPEC['gpdk_root']]*8


@pytest.mark.parametrize('update',[{'library':'Two_Stage_Opamp'},{'library':'gpdk045'},
    {'cell':'../bad'},{'gpdk_root':'relative'},{'task_ref':False},{'overwrite':True},
    {'presentation':'background'},{'request_id':'bad)exit('}])
def test_invalid_rebuild_input_never_dispatches(monkeypatch,update):
    monkeypatch.setattr('cadai.circuit_rebuild.qualify_gpdk',lambda root: None)
    with pytest.raises(CircuitArgumentError):
        build_circuit_skill('preview_opamp_rebuild',{**SPEC,**update})


def test_unavailable_profile_and_paired_source(monkeypatch):
    def reject(root): raise CircuitArgumentError('profile changed')
    monkeypatch.setattr('cadai.circuit_rebuild.qualify_gpdk',reject)
    for name,key in [('create_opamp_dut','preview_ref'),('create_opamp_rebuild_maestro','circuit_ref'),('run_opamp_rebuild','setup_ref')]:
        with pytest.raises(CircuitArgumentError,match='profile changed'):
            build_circuit_skill(name,{key:'ref:a','request_id':'r','gpdk_root':SPEC['gpdk_root']})
    monkeypatch.setattr('cadai.circuit_rebuild.qualify_gpdk',lambda root: None)
    with pytest.raises(CircuitArgumentError,match='paired'):
        build_circuit_skill('preview_opamp_rebuild',SPEC)


def test_rebuild_verification_profile_when_available():
    import inspect
    if 'profile' not in inspect.signature(McpServer).parameters:
        pytest.skip('verification profile extension is not installed')
    for definition in REBUILD_TOOLS:
        assert call(McpServer(Client(),profile='verification'),definition['name'],{})['error']['code']==-32602


def test_netlist_comparison_preserves_scope_nodes_and_parameters():
    from opamp_netlist_compare import canonical_netlist
    base='subckt OpAmp A B\nR0 (A B) resistor r=1k\nC0 (B 0) capacitor c=1p\nends OpAmp\nI0 (IN OUT) OpAmp\n'
    renamed='// comment\nsubckt new A B\nC0 (B 0) capacitor c=1p\nR0 (A B) resistor r=1k\nends new\nI_new_0 (IN OUT) new\n'
    assert canonical_netlist(base)==canonical_netlist(renamed,'new')
    for altered in [base.replace('r=1k','r=2k'),base.replace('(A B)','(B A)'),
                    base.replace('R0 (A B) resistor r=1k\n','').replace('I0 (','R0 (A B) resistor r=1k\nI0 (')]:
        assert canonical_netlist(base)!=canonical_netlist(altered)
