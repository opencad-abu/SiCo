"""Licensed gate acceptance via actual MCP/controller/native CIW and OA readback."""
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import time
import uuid

import pytest

from cadai.controller import RequestDispatcher
from cadai.mcp import McpServer
from cadai.runtime import RuntimePaths
from test_circuit_rc_gui import NativeBridge, DispatchClient, mcp_call, AI_ROOT, IC_LIBS
from test_circuit_gate import ROOT


def test_gate_foreground_background_and_refusal(tmp_path):
    if os.environ.get('CAD_AI_RUN_GUI_TESTS') != '1':
        pytest.skip('set CAD_AI_RUN_GUI_TESTS=1 for licensed gpdk gate acceptance')
    virtuoso, xvfb = shutil.which('virtuoso'), shutil.which('Xvfb')
    if not virtuoso or not xvfb or not ROOT.exists():
        pytest.skip('Virtuoso/Xvfb/gpdk045 v3.5 unavailable')
    lib = 'gateMcp'+uuid.uuid4().hex[:8]
    (tmp_path/'cds.lib').write_text(f'DEFINE analogLib {IC_LIBS}/artist/analogLib\n'
        f'DEFINE basic {IC_LIBS}/basic\nDEFINE gpdk045 {ROOT}/gpdk045\n')
    modules = ['AI_protocol.il','AI_eval.il','AI_circuit.il','AI_circuit_context.il',
        'AI_circuit_task.il','AI_circuit_rc.il','AI_circuit_symbol.il','AI_circuit_gate_devices.il',
        'AI_circuit_gate_verify.il','AI_circuit_gate.il','AI_circuit_config.il']
    lines = ['load('+json.dumps(str(AI_ROOT/'skill'/m))+')' for m in modules]
    lines += [f'ddCreateLib("{lib}" {json.dumps(str(tmp_path/"library"))})',
        'procedure(gateTestPoll() when(isFile("command.il") load("command.il") deleteFile("command.il")) '
        'hiRegTimer("gateTestPoll()" 1))','gateTestPoll()','aiWriteTextFile("ready" "yes")']
    (tmp_path/'run.il').write_text('\n'.join(lines)+'\n')
    rfd,wfd = os.pipe()
    xprocess = subprocess.Popen([xvfb,'-displayfd',str(wfd),'-screen','0','1440x1000x24','-nolisten','tcp'],
        pass_fds=(wfd,),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    os.close(wfd); process = None
    try:
        assert select.select([rfd],[],[],10)[0]
        display = os.read(rfd,32).decode().strip(); assert display.isdigit()
        with (tmp_path/'process.log').open('w') as log:
            process = subprocess.Popen([virtuoso,'-nocdsinit','-norestore','-replay','run.il','-log','CDS.log'],
                cwd=tmp_path,env=dict(os.environ,DISPLAY=':'+display),stdout=log,stderr=subprocess.STDOUT,
                start_new_session=True)
        bridge = NativeBridge(tmp_path,process); bridge.wait_file(tmp_path/'ready')
        spool = tmp_path/'spool'; spool.mkdir(mode=0o700)
        server = McpServer(DispatchClient(RequestDispatcher(bridge,
            RuntimePaths(tmp_path,tmp_path/'socket',spool),tmp_path)))
        bridge.skill('progn(load('+json.dumps(str(Path(__file__).parent/'fixtures/gpdk_gate_acceptance.il'))+
            ') envSetVal("asimenv.startup" "projectDir" \'string '+json.dumps(str(tmp_path/'simulation'))+'))')
        # Load the PDK before taking the effective CDF snapshot.
        bridge.skill('let((m) m=dbOpenCellViewByType("gpdk045" "nmos1v" "symbol" "" "r") dbClose(m))')
        cdfcode = 'aiCircuitJson(foreach(mapcar c list("nmos1v" "pmos1v") foreach(mapcar p cdfGetCellCDF(ddGetObj("gpdk045" c))~>parameters list(p~>name p~>value))))'
        before = bridge.skill(cdfcode)
        evidence=[]
        for mode in ('foreground','background'):
            task=mcp_call(server,'begin_circuit_task',dict(task_id=mode,presentation=mode,choice_confirmed=True))
            for gate in ('INV','NAND2'):
                cell=gate.lower()+'_'+mode
                spec=dict(library=lib,cell=cell,gate=gate,gpdk_root=str(ROOT))
                preview=mcp_call(server,'preview_gpdk_gate',spec)
                args={**spec,'task_ref':task['task_ref'],'request_id':cell,'preview_digest':preview['preview_digest']}
                result=mcp_call(server,'create_gpdk_gate',args)
                assert result['ok'] and result['saved'],result
                assert result['interface_check']==[0,0]
                assert [row[1] for row in result['checks']]==[[0,0],'ports_verified',[0,0]]
                assert len(result['cdf_readback'])==(2 if gate=='INV' else 4)
                symbol_info=json.loads(bridge.skill('let((v info) v=dbOpenCellViewByType("'+lib+'" "'+cell+'" "symbol" "" "r") '
                    'info=aiCircuitJson(list(aiCircuitSymbolPinBox(v) foreach(mapcar s v~>shapes list(s~>lpp s~>bBox s~>theLabel s~>labelType)))) dbClose(v) info)'))
                # Selection box includes the symbol body outline as well as pin centers;
                # terminal stubs and labels remain outside the instance box.
                expected_box=[[-0.25,-1.0],[1.25,1.0]]
                assert symbol_info[0]==expected_box
                assert any(row[1]==expected_box for row in symbol_info[1])
                annotations=[row for row in symbol_info[1] if row[2] and row[2].startswith('cdsTerm(')]
                assert {row[2] for row in annotations}=={'cdsTerm("'+name+'")' for name in
                    (['A','B','Y','VDD','VSS'] if gate=='NAND2' else ['A','Y','VDD','VSS'])}
                assert all(row[3]=='ILLabel' for row in annotations)
                assert mcp_call(server,'create_gpdk_gate',args)==result
                assert mcp_call(server,'inspect_gpdk_gate',dict(circuit_ref=result['circuit_ref']))['ok']
                exists=mcp_call(server,'create_gpdk_gate',{**args,'request_id':cell+'-exists'})
                assert exists['ok'] is False and exists['stage']=='preflight'
                conflict=mcp_call(server,'create_gpdk_gate',{**args,'task_ref':task['task_ref']+'-missing'})
                assert conflict['code']=='task_not_found'
                conflict=mcp_call(server,'create_gpdk_gate',{**args,'cell':cell+'x',
                    'preview_digest':mcp_call(server,'preview_gpdk_gate',{**spec,'cell':cell+'x'})['preview_digest']})
                assert conflict['code']=='request_conflict_or_limit'
                cfg_args=dict(library=lib,cell=cell+'_tb',top_library=lib,top_cell=cell+'_tb',
                    task_ref=task['task_ref'],request_id=cell,bindings=[dict(instance='I_'+cell+'_0',
                    library=lib,cell=cell,view='schematic')])
                config=mcp_call(server,'create_circuit_config',cfg_args)
                assert config['ok'] and config['saved'],config
                assert config['hierarchy_complete'] is False
                dut_binding=next(row for row in config['bindings'] if row[0]=='I_'+cell+'_0')
                assert dut_binding[4:]==[lib,cell,'schematic']
                assert mcp_call(server,'create_circuit_config',cfg_args)==config
                assert mcp_call(server,'inspect_circuit_config',dict(config_ref=config['config_ref']))['ok']
                assert mcp_call(server,'create_circuit_config',{**cfg_args,'request_id':cell+'-exists'})['ok'] is False
                bad={**cfg_args,'view':'bad_config','request_id':cell+'-missing',
                    'bindings':[dict(instance='I_missing_0',library=lib,cell=cell,view='schematic')]}
                assert mcp_call(server,'create_circuit_config',bad)['ok'] is False
                assert bridge.skill('ddGetObj("'+lib+'" "'+cell+'_tb" "bad_config")')=='nil'
                if mode=='foreground':
                    session=bridge.skill('gateAcceptanceSetup("'+lib+'" "'+cell+'_tb" '+json.dumps(str(ROOT))+' "config")').strip('"')
                    history=bridge.skill('maeRunSimulation(?session '+json.dumps(session)+
                        ' ?callback "gateAcceptanceCallback" ?waitUntilDone nil)').strip('"')
                    assert history.startswith('Interactive.')
                    deadline=time.monotonic()+100
                    while bridge.skill('gateAcceptanceDone['+json.dumps(session)+']')=='nil':
                        assert time.monotonic()<deadline,'simulation timeout'
                        time.sleep(0.5)
                    sim=json.loads(bridge.skill('gateAcceptanceResults('+json.dumps(session)+' '+json.dumps(history)+')'))
                    assert sim['errors'] in ('',None)
                    expected={'A':[0,0,1,1],'Y':[1,1,0,0] if gate=='INV' else [1,1,1,0]}
                    if gate=='NAND2': expected['B']=[0,1,0,1]
                    values={row[0]:row[1] for row in sim['rows']}
                    assert len(sim['rows'])==len(values)==4*len(expected)
                    assert all(row[2:]==['nominal','GATE',1] for row in sim['rows'])
                    for net,seq in expected.items():
                        for idx,value in enumerate(seq):
                            assert values[net+str(idx)]==pytest.approx(value,abs=0.05)
                    (tmp_path/(gate+'-simulation.json')).write_text(json.dumps(sim,indent=2))
                    netlist=(tmp_path/'simulation'/lib/(cell+'_tb')/'maestro/results/maestro'/history/'1/GATE/netlist/input.scs').read_text()
                    assert 'g45p1svt w=(480n) l=45n nf=1 as=67.2f ad=67.2f' in netlist
                    assert 'I_'+cell+'_0 (' in netlist
                    assert 'MN0 (Y A '+('VSS' if gate=='INV' else 'NINT')+' VSS) g45n1svt' in netlist
                    if gate=='NAND2': assert 'MN1 (NINT B VSS VSS) g45n1svt' in netlist
                    bridge.skill('dbReplaceProp(geGetWindowCellView(nth(3 car(aiGateCircuits["gate:'+cell+'"][\'views]))) "userEdit" "string" "unsaved")')
                    assert mcp_call(server,'inspect_gpdk_gate',dict(circuit_ref=result['circuit_ref']))['code']=='gate_changed'
                    # Restore only this test's own edit for orderly shutdown.
                    bridge.skill('let((v) v=geGetWindowCellView(nth(3 car(aiGateCircuits["gate:'+cell+'"][\'views]))) dbDeletePropByName(v "userEdit") dbSave(v))')
                else:
                    bridge.skill('let((h) h=hdbOpen("'+lib+'" "'+cell+'_tb" "config" "a") '
                        'hdbSetDefaultViewListString(h "schematic spectre") hdbSave(h) hdbClose(h))')
                    assert mcp_call(server,'inspect_circuit_config',dict(config_ref=config['config_ref']))['code']=='config_changed'
                    # A saved model/width change must be refused, even with same geometry.
                    bridge.skill('let((v i) v=dbOpenCellViewByType("'+lib+'" "'+cell+'" "schematic" "" "a") '
                        'i=dbFindAnyInstByName(v "MN0") dbReplaceProp(i "w" "string" "960n") dbSave(v) dbClose(v))')
                    assert mcp_call(server,'inspect_gpdk_gate',dict(circuit_ref=result['circuit_ref']))['code']=='gate_changed'
                evidence.append(dict(circuit=result,config=config))
        assert bridge.skill(cdfcode)==before, 'effective source CDF changed'
        (tmp_path/'acceptance.json').write_text(json.dumps(evidence,indent=2))
        bridge.skill('foreach(s maeGetSessions() maeSaveSetup(?session s))')
        bridge.skill('hiRegTimer("exit()" 5)')
        assert process.wait(timeout=25)==0
    finally:
        os.close(rfd)
        if process and process.poll() is None:
            os.killpg(process.pid,signal.SIGTERM)
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL); process.wait(timeout=5)
        xprocess.terminate(); xprocess.wait(timeout=5)
