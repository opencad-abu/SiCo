"""Licensed reference Save As -> native MCP -> Spectre -> dimensioned RDB regression."""
import hashlib
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
from test_circuit_gate import ROOT
from test_circuit_rc_gui import NativeBridge, DispatchClient, mcp_call, AI_ROOT, IC_LIBS

SOURCE=ROOT.parent/'libs/Two_Stage_Opamp'


def source_hashes():
    # Hash Cadence-owned data; do not parse or copy OA internals.
    return {str(p.relative_to(SOURCE)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in SOURCE.rglob('*') if p.is_file() and p.suffix in ('.oa','.sdb','.dm')}


def test_opamp_reference_copy_lifecycle_and_results(tmp_path):
    if os.environ.get('CAD_AI_RUN_GUI_TESTS')!='1':
        pytest.skip('set CAD_AI_RUN_GUI_TESTS=1 for licensed OpAmp acceptance')
    virtuoso,xvfb=shutil.which('virtuoso'),shutil.which('Xvfb')
    if not virtuoso or not xvfb or not SOURCE.exists(): pytest.skip('Virtuoso/Xvfb/RAK unavailable')
    library='opampMcp'+uuid.uuid4().hex[:8]
    (tmp_path/'cds.lib').write_text(f'DEFINE analogLib {IC_LIBS}/artist/analogLib\nDEFINE basic {IC_LIBS}/basic\n'
        f'DEFINE gpdk045 {ROOT}/gpdk045\nDEFINE Two_Stage_Opamp {SOURCE}\n')
    modules=['AI_protocol','AI_eval','AI_circuit','AI_circuit_context','AI_circuit_task','AI_circuit_rc',
        'AI_circuit_gate_devices','AI_circuit_gate_verify','AI_circuit_opamp_contract','AI_circuit_opamp','AI_circuit_opamp_run','AI_circuit_opamp_results']
    lines=['load('+json.dumps(str(AI_ROOT/'skill'/(m+'.il')))+')' for m in modules]
    lines += [f'ddCreateLib("{library}" {json.dumps(str(tmp_path/"library"))})',
        'envSetVal("asimenv.startup" "projectDir" \'string '+json.dumps(str(tmp_path/'simulation'))+')',
        'procedure(opampTestPoll() when(isFile("command.il") load("command.il") deleteFile("command.il")) '
        'hiRegTimer("opampTestPoll()" 1))','opampTestPoll()','aiWriteTextFile("ready" "yes")']
    (tmp_path/'run.il').write_text('\n'.join(lines)+'\n')
    before=source_hashes()
    rfd,wfd=os.pipe()
    xprocess=subprocess.Popen([xvfb,'-displayfd',str(wfd),'-screen','0','1440x1000x24','-nolisten','tcp'],
        pass_fds=(wfd,),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    os.close(wfd); process=None
    try:
        assert select.select([rfd],[],[],10)[0]
        display=os.read(rfd,32).decode().strip(); assert display.isdigit()
        with (tmp_path/'process.log').open('w') as log:
            process=subprocess.Popen([virtuoso,'-nocdsinit','-norestore','-replay','run.il','-log','CDS.log'],cwd=tmp_path,
                env=dict(os.environ,DISPLAY=':'+display),stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        bridge=NativeBridge(tmp_path,process); bridge.wait_file(tmp_path/'ready')
        spool=tmp_path/'spool'; spool.mkdir(mode=0o700)
        server=McpServer(DispatchClient(RequestDispatcher(bridge,RuntimePaths(tmp_path,tmp_path/'socket',spool),tmp_path)))
        results=[]
        for mode in ('foreground','background'):
            task=mcp_call(server,'begin_circuit_task',dict(task_id=mode,presentation=mode,choice_confirmed=True))
            args=dict(library=library,cell=mode,task_ref=task['task_ref'],request_id=mode,gpdk_root=str(ROOT))
            created=mcp_call(server,'create_opamp_reference_maestro',args)
            assert created['ok'] and created['saved'],created
            assert created['circuit_created'] is False and created['model_section']=='mc'
            ref={'setup_ref':created['setup_ref']}
            assert mcp_call(server,'create_opamp_reference_maestro',args)==created
            assert mcp_call(server,'inspect_opamp_reference_maestro',ref)['ok']
            assert mcp_call(server,'create_opamp_reference_maestro',{**args,'request_id':mode+'exists'})['ok'] is False
            runargs={**ref,'request_id':mode,'gpdk_root':str(ROOT)}
            run=mcp_call(server,'run_opamp_reference',runargs)
            assert run['state']=='running',run
            repeat=mcp_call(server,'run_opamp_reference',runargs)
            assert repeat['run_ref']==run['run_ref'] and repeat['history']==run['history']
            deadline=time.monotonic()+120
            while time.monotonic()<deadline:
                status=mcp_call(server,'get_opamp_reference_status',{'run_ref':run['run_ref']})
                if status['state']!='running': break
                time.sleep(.5)
            assert status['state']=='completed',status
            measured=mcp_call(server,'read_opamp_reference_results',{'run_ref':run['run_ref']})
            assert measured['ok'] and measured['all_scalar'] and not measured['spec_qualified'],measured
            assert len(measured['rows'])==9
            assert all(row[2:4]==['nominal',1] and row[6]=='measured' for row in measured['rows'])
            values={(r[0],r[1]):r[4] for r in measured['rows']}
            assert values['AC','Gain']==pytest.approx(46.740437862867,rel=.001)
            assert values['AC','UGF']==pytest.approx(546392794.902879,rel=.001)
            assert values['TRAN','Swing']==pytest.approx(1.00759421641901,rel=.001)
            assert values['TRAN','PhaseMargin']==pytest.approx(20.706423916134,rel=.001)
            for test in ('AC','TRAN'):
                net=(tmp_path/'simulation'/library/mode/'maestro/results/maestro'/run['history']/'1'/test/'netlist/input.scs').read_text()
                assert f'include "{ROOT}/models/spectre/gpdk045.scs" section=mc' in net
                assert 'vdd=1.3' in net
                if test=='TRAN': assert 'stop=30n' in net and 'stb stb' in net
            session=json.dumps(created['session'])
            bridge.skill('maeSetVar("vdd" "1.4" ?session '+session+')')
            assert mcp_call(server,'inspect_opamp_reference_maestro',ref)['code']=='reference_changed'
            denied=mcp_call(server,'run_opamp_reference',{**runargs,'request_id':mode+'edited'})
            assert denied['state']=='failed'
            # Restore only this test-owned edit; production never saves a refused change.
            bridge.skill('progn(maeSetVar("vdd" "1.3" ?session '+session+') maeSaveSetup(?session '+session+'))')
            assert mcp_call(server,'inspect_opamp_reference_maestro',ref)['ok']
            results.append(dict(setup=created,run=status,measurements=measured))
            if mode=='background':
                cancel_run=mcp_call(server,'run_opamp_reference',{**runargs,'request_id':'cancel'})
                assert cancel_run['state']=='running',cancel_run
                cancel_ref={'run_ref':cancel_run['run_ref']}
                assert mcp_call(server,'release_opamp_reference',ref)['code']=='release_failed'
                stopped=mcp_call(server,'stop_opamp_reference',cancel_ref)
                assert stopped['state']=='stop_requested',stopped
                deadline=time.monotonic()+100
                while time.monotonic()<deadline:
                    stopped=mcp_call(server,'get_opamp_reference_status',cancel_ref)
                    if stopped['state']!='stop_requested': break
                    time.sleep(.5)
                assert stopped['state']=='stopped',stopped
                assert mcp_call(server,'read_opamp_reference_results',cancel_ref)['code']=='run_not_completed'
                assert mcp_call(server,'read_opamp_reference_results',{'run_ref':run['run_ref']})==measured
                results[-1]['cancellation']=stopped
            assert mcp_call(server,'release_opamp_reference',ref)['stage']=='released'
            retained=json.loads(bridge.skill('aiCircuitJson(maeGetSessions())')) or []
            assert (created['session'] in retained)==(mode=='foreground')
        assert before==source_hashes(),'source Cadence data changed'
        (tmp_path/'acceptance.json').write_text(json.dumps(results,indent=2))
        bridge.skill('hiRegTimer("exit()" 5)'); assert process.wait(timeout=25)==0
    finally:
        os.close(rfd)
        if process and process.poll() is None:
            os.killpg(process.pid,signal.SIGTERM)
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: os.killpg(process.pid,signal.SIGKILL); process.wait(timeout=5)
        xprocess.terminate(); xprocess.wait(timeout=5)
