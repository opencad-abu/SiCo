"""Licensed end-to-end rebuild -> config -> Maestro -> Spectre equivalence."""
import json
import os
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
from test_circuit_opamp_gui import SOURCE, source_hashes
from test_circuit_rc_gui import NativeBridge, DispatchClient, mcp_call, AI_ROOT, IC_LIBS
from opamp_netlist_compare import canonical_netlist


def test_rebuild_foreground_background_equivalence(tmp_path):
    if os.environ.get('CAD_AI_RUN_GUI_TESTS')!='1':
        pytest.skip('set CAD_AI_RUN_GUI_TESTS=1 for licensed rebuild acceptance')
    virtuoso,xvfb=shutil.which('virtuoso'),shutil.which('Xvfb')
    if not virtuoso or not xvfb or not SOURCE.exists(): pytest.skip('Virtuoso/Xvfb/RAK unavailable')
    library='rebuildMcp'+uuid.uuid4().hex[:8]
    (tmp_path/'cds.lib').write_text(f'DEFINE analogLib {IC_LIBS}/artist/analogLib\nDEFINE basic {IC_LIBS}/basic\n'
        f'DEFINE gpdk045 {ROOT}/gpdk045\nDEFINE Two_Stage_Opamp {SOURCE}\nDEFINE referenceAlias {SOURCE}\n')
    modules=['AI_protocol','AI_eval','AI_circuit','AI_circuit_context','AI_circuit_task','AI_circuit_rc',
        'AI_circuit_symbol','AI_circuit_gate_devices','AI_circuit_gate_verify','AI_circuit_gate','AI_circuit_config',
        'AI_circuit_opamp_contract','AI_circuit_opamp','AI_circuit_opamp_run','AI_circuit_opamp_results',
        'AI_circuit_rebuild_source','AI_circuit_rebuild_devices','AI_circuit_rebuild','AI_circuit_rebuild_maestro']
    lines=['load('+json.dumps(str(AI_ROOT/'skill'/(m+'.il')))+')' for m in modules]
    lines += [f'ddCreateLib("{library}" {json.dumps(str(tmp_path/"library"))})',
        'envSetVal("asimenv.startup" "projectDir" \'string '+json.dumps(str(tmp_path/'simulation'))+')',
        'procedure(rebuildTestPoll() when(isFile("command.il") load("command.il") deleteFile("command.il")) '
        'hiRegTimer("rebuildTestPoll()" 1))','rebuildTestPoll()','aiWriteTextFile("ready" "yes")']
    (tmp_path/'run.il').write_text('\n'.join(lines)+'\n')
    before=source_hashes(); rfd,wfd=os.pipe(); process=None
    xprocess=subprocess.Popen([xvfb,'-displayfd',str(wfd),'-screen','0','1600x1000x24','-nolisten','tcp'],
        pass_fds=(wfd,),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    os.close(wfd)
    try:
        assert select.select([rfd],[],[],10)[0]
        display=os.read(rfd,32).decode().strip(); assert display.isdigit()
        with (tmp_path/'process.log').open('w') as log:
            process=subprocess.Popen([virtuoso,'-nocdsinit','-norestore','-replay','run.il','-log','CDS.log'],cwd=tmp_path,
                env=dict(os.environ,DISPLAY=':'+display),stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        bridge=NativeBridge(tmp_path,process); bridge.wait_file(tmp_path/'ready')
        spool=tmp_path/'spool'; spool.mkdir(mode=0o700)
        server=McpServer(DispatchClient(RequestDispatcher(bridge,RuntimePaths(tmp_path,tmp_path/'socket',spool),tmp_path)))
        def call(name,**args): return mcp_call(server,name,args)
        def run(setup,request,name='run_opamp_reference'):
            args=dict(setup_ref=setup['setup_ref'],request_id=request,gpdk_root=str(ROOT))
            started=call(name,**args); assert started['state']=='running',started
            repeat=call(name,**args)
            assert (repeat['run_ref'],repeat['history'])==(started['run_ref'],started['history'])
            end=time.monotonic()+150
            while time.monotonic()<end:
                status=call('get_opamp_reference_status',run_ref=started['run_ref'])
                if status['state']!='running': break
                time.sleep(.5)
            assert status['state']=='completed',status
            result=call('read_opamp_reference_results',run_ref=started['run_ref'])
            assert result['ok'] and result['all_scalar'] and len(result['rows'])==9,result
            nets={test:(tmp_path/'simulation'/library/setup['target'][1]/'maestro/results/maestro'/started['history']/'1'/test/'netlist/input.scs').read_text() for test in ('AC','TRAN')}
            return status,result,nets
        task=call('begin_circuit_task',task_id='baseline',presentation='background',choice_confirmed=True)
        alias=call('preview_opamp_rebuild',library='referenceAlias',cell='alias_target',task_ref=task['task_ref'],request_id='alias',gpdk_root=str(ROOT))
        assert alias['code']=='preview_failed',alias
        base=call('create_opamp_reference_maestro',library=library,cell='baseline',task_ref=task['task_ref'],request_id='base',gpdk_root=str(ROOT))
        assert base['ok'],base
        baseline,expected,reference_nets=run(base,'baseline')
        assert call('release_opamp_reference',setup_ref=base['setup_ref'])['stage']=='released'
        evidence=[]
        for mode in ('foreground','background'):
            task=call('begin_circuit_task',task_id=mode,presentation=mode,choice_confirmed=True)
            preview_args=dict(library=library,cell=mode,task_ref=task['task_ref'],request_id=mode,gpdk_root=str(ROOT))
            preview=call('preview_opamp_rebuild',**preview_args)
            assert preview['ok'],preview
            assert call('preview_opamp_rebuild',**preview_args)==preview
            assert call('preview_opamp_rebuild',**{**preview_args,'cell':'conflict'})['ok'] is False
            args=dict(preview_ref=preview['preview_ref'],request_id=mode,gpdk_root=str(ROOT))
            built=call('create_opamp_dut',**args)
            assert built['ok'] and built['saved'],built
            assert built['interface_check']==[0,0]
            assert [row[1] for row in built['checks'] if row[0][2]=='schematic']==[[0,0]]*3
            assert len(built['targets'])==4
            # All voltage/current stimuli on the left; capacitive/resistive loads right.
            rows=json.loads(bridge.skill('aiCircuitJson(let((cv result) '
                +f'foreach(cell list("{mode}_ac_tb" "{mode}_tran_tb") '
                +f'cv=dbOpenCellViewByType("{library}" cell "schematic" "" "r") '
                +'foreach(i cv~>instances when(i~>libName=="analogLib" '
                +'result=cons(list(i~>cellName xCoord(i~>xy)) result))) dbClose(cv)) result))'))
            for master,x in rows:
                if master in ('vdc','vpulse','idc'): assert x<3
                if master in ('res','cap'): assert x>7
            assert call('create_opamp_dut',**args)==built
            assert call('create_opamp_dut',**{**args,'request_id':mode+'exists'})['ok'] is False
            assert call('inspect_opamp_dut',circuit_ref=built['circuit_ref'])['ok']
            setup_args=dict(circuit_ref=built['circuit_ref'],request_id=mode,gpdk_root=str(ROOT))
            setup=call('create_opamp_rebuild_maestro',**setup_args)
            assert setup['ok'] and setup['saved'] and setup['circuit_created'],setup
            assert call('create_opamp_rebuild_maestro',**setup_args)==setup
            assert call('create_opamp_rebuild_maestro',**{**setup_args,'request_id':mode+'exists'})['ok'] is False
            status,result,nets=run(setup,mode,'run_opamp_rebuild')
            for refrow,row in zip(expected['rows'],result['rows']):
                assert row[:4]==refrow[:4] and row[5:]==refrow[5:]
                assert row[4]==pytest.approx(refrow[4],rel=1e-7,abs=1e-18)
            for test in ('AC','TRAN'):
                assert canonical_netlist(nets[test],mode)==canonical_netlist(reference_nets[test])
            # A user geometry edit must be refused, without implicit check/save.
            bridge.skill(f'procedure(rebuildTestEdit() let((cv) cv=dbOpenCellViewByType("{library}" "{mode}" "symbol" "" "a") '
                         'rebuildTestNote=schCreateNoteLabel(cv 0:2 "temporary user edit" "lowerLeft" "R0" "fixed" 0.125 "normalLabel") '
                         'dbClose(cv) t))')
            bridge.skill('rebuildTestEdit()')
            assert call('inspect_opamp_dut',circuit_ref=built['circuit_ref'])['code']=='dut_changed'
            refused=call('run_opamp_rebuild',setup_ref=setup['setup_ref'],request_id=mode+'edited',gpdk_root=str(ROOT))
            assert refused['state']=='failed'
            # Restore only this test's note, then save explicitly and revalidate.
            bridge.skill(f'procedure(rebuildTestRestore() let((cv) cv=dbOpenCellViewByType("{library}" "{mode}" "symbol" "" "a") '
                         'dbDeleteObject(rebuildTestNote) dbSave(cv) dbClose(cv) t))')
            bridge.skill('rebuildTestRestore()')
            assert call('inspect_opamp_dut',circuit_ref=built['circuit_ref'])['ok']
            assert call('inspect_opamp_reference_maestro',setup_ref=setup['setup_ref'])['ok']
            # A changed config must also invalidate the run, including saved edits.
            bridge.skill(f'procedure(rebuildTestConfig(views) let((h) h=hdbOpen("{library}" "{mode}_ac_tb" "config" "a") '
                         'hdbSetDefaultViewListString(h views) hdbSave(h) hdbClose(h) t))')
            bridge.skill('rebuildTestConfig("schematic spectre")')
            assert call('inspect_opamp_reference_maestro',setup_ref=setup['setup_ref'])['code']=='reference_changed'
            bridge.skill('rebuildTestConfig("spectre schematic")')
            assert call('inspect_opamp_reference_maestro',setup_ref=setup['setup_ref'])['ok']
            assert call('release_opamp_reference',setup_ref=setup['setup_ref'])['stage']=='released'
            sessions=json.loads(bridge.skill('aiCircuitJson(maeGetSessions())')) or []
            assert (setup['session'] in sessions)==(mode=='foreground')
            evidence.append(dict(built=built,setup=setup,status=status,results=result,netlists_equivalent=True))
        assert before==source_hashes(),'source OA data changed'
        (tmp_path/'acceptance.json').write_text(json.dumps(dict(baseline=baseline,cases=evidence),indent=2))
        bridge.skill('hiRegTimer("exit()" 5)'); assert process.wait(timeout=25)==0
    finally:
        os.close(rfd)
        if process and process.poll() is None:
            os.killpg(process.pid,signal.SIGTERM)
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: os.killpg(process.pid,signal.SIGKILL); process.wait(timeout=5)
        xprocess.terminate(); xprocess.wait(timeout=5)
