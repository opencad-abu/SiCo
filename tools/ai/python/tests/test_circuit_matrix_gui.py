"""Licensed corner/load matrix, reference preservation and rebuilt equivalence."""
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


def test_matrix_corner_sweep_and_reference_equivalence(tmp_path):
    if os.environ.get('CAD_AI_RUN_GUI_TESTS')!='1':
        pytest.skip('set CAD_AI_RUN_GUI_TESTS=1 for licensed rebuild acceptance')
    virtuoso,xvfb=shutil.which('virtuoso'),shutil.which('Xvfb')
    if not virtuoso or not xvfb or not SOURCE.exists(): pytest.skip('Virtuoso/Xvfb/RAK unavailable')
    library='matrixMcp'+uuid.uuid4().hex[:8]
    (tmp_path/'cds.lib').write_text(f'DEFINE analogLib {IC_LIBS}/artist/analogLib\nDEFINE basic {IC_LIBS}/basic\n'
        f'DEFINE gpdk045 {ROOT}/gpdk045\nDEFINE Two_Stage_Opamp {SOURCE}\nDEFINE referenceAlias {SOURCE}\n')
    modules=['AI_protocol','AI_eval','AI_circuit','AI_circuit_context','AI_circuit_task','AI_circuit_rc',
        'AI_circuit_symbol','AI_circuit_gate_devices','AI_circuit_gate_verify','AI_circuit_gate','AI_circuit_config',
        'AI_circuit_opamp_contract','AI_circuit_opamp','AI_circuit_opamp_run','AI_circuit_opamp_results',
        'AI_circuit_rebuild_source','AI_circuit_rebuild_devices','AI_circuit_rebuild','AI_circuit_rebuild_maestro','AI_circuit_matrix','AI_circuit_matrix_results']
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
        def execute(setup,request):
            args=dict(setup_ref=setup['setup_ref'],request_id=request,gpdk_root=str(ROOT))
            run=call('run_opamp_matrix',**args)
            assert run['state']=='running' and run['expected_test_points']==24,run
            again=call('run_opamp_matrix',**args)
            assert (run['history'],run['run_ref'])==(again['history'],again['run_ref'])
            assert call('release_opamp_reference',setup_ref=setup['setup_ref'])['code']=='release_failed'
            end=time.monotonic()+240
            while time.monotonic()<end:
                status=call('get_opamp_reference_status',run_ref=run['run_ref'])
                if status['state']!='running': break
                time.sleep(.5)
            assert status['state']=='completed' and status['progress']==[24,24],status
            result=call('read_opamp_matrix_results',run_ref=run['run_ref'])
            assert result['ok'] and result['all_scalar'] and not result['spec_qualified'],result
            assert len(result['rows'])==108
            assert all(r[8]=='measured' for r in result['rows'])
            assert call('read_opamp_reference_results',run_ref=run['run_ref'])==result
            directory=tmp_path/'simulation'/library/setup['target'][1]/'maestro/results/maestro'/run['history']
            # Pick one real generated netlist for each point/corner/test, not PSF aliases.
            nets={}
            import re
            for path in directory.rglob('input.scs'):
                if 'psf' in path.relative_to(directory).parts: continue
                text=path.read_text()
                section=re.search(r'include "[^"\n]*gpdk045.scs" section=(tt|ff|ss)',text)
                assert section,text[:500]
                cap=float(re.search(r'\bcload=([0-9.eE+-]+)',text).group(1))
                res=float(re.search(r'\brload=([0-9.eE+-]+)',text).group(1))
                test=path.parent.parent.name
                key=(test,section.group(1),cap,res)
                assert key not in nets,key
                nets[key]=canonical_netlist(text,'rebuilt')
            assert len(nets)==24,len(nets)
            return result,nets,status
        bg=call('begin_circuit_task',task_id='bg',presentation='background',choice_confirmed=True)
        source=call('create_opamp_reference_maestro',library=library,cell='source',task_ref=bg['task_ref'],request_id='source',gpdk_root=str(ROOT))
        assert source['ok'],source
        def snapshot(setup):
            session=json.dumps(setup['session'])
            return bridge.skill('aiCircuitJson(list(aiOpampSetupSnapshot('+session+') aiMatrixSnapshot('+session+')))')
        source_snapshot=snapshot(source)
        matargs=dict(setup_ref=source['setup_ref'],cell='reference_matrix',request_id='reference',gpdk_root=str(ROOT),cload_values_f=[1e-13,2e-13],rload_values_ohm=[1000,2000])
        ref=call('create_opamp_matrix',**matargs)
        assert ref['ok'] and ref['saved'],ref
        assert ref['model_section']=='tt/ff/ss' and ref['model_sections']==['tt','ff','ss']
        assert snapshot(source)==source_snapshot
        assert call('create_opamp_matrix',**matargs)==ref
        assert call('create_opamp_matrix',**{**matargs,'cell':'conflict'})['code']=='request_conflict_or_limit'
        # Deliberate existing-target refusal (the CIW error is expected).
        assert call('create_opamp_matrix',**{**matargs,'request_id':'exists'})['ok'] is False
        assert call('inspect_opamp_reference_maestro',setup_ref=source['setup_ref'])['ok']
        expected,nets,ref_status=execute(ref,'reference')
        assert call('release_opamp_reference',setup_ref=ref['setup_ref'])['stage']=='released'
        fg=call('begin_circuit_task',task_id='fg',presentation='foreground',choice_confirmed=True)
        preview=call('preview_opamp_rebuild',library=library,cell='rebuilt',task_ref=fg['task_ref'],request_id='rebuild',gpdk_root=str(ROOT))
        built=call('create_opamp_dut',preview_ref=preview['preview_ref'],request_id='rebuild',gpdk_root=str(ROOT))
        assert built['ok'],built
        nominal=call('create_opamp_rebuild_maestro',circuit_ref=built['circuit_ref'],request_id='nominal',gpdk_root=str(ROOT))
        assert nominal['ok'],nominal
        nominal_snapshot=snapshot(nominal)
        made=call('create_opamp_matrix',**{**matargs,'setup_ref':nominal['setup_ref'],'cell':'rebuilt_matrix','request_id':'rebuilt'})
        assert made['ok'],made
        assert snapshot(nominal)==nominal_snapshot
        assert call('inspect_opamp_reference_maestro',setup_ref=nominal['setup_ref'])['ok']
        result,actual_nets,status=execute(made,'rebuilt')
        assert nets==actual_nets
        def key(row): return tuple(row[:3]+row[4:6])
        expected_rows={key(r):r for r in expected['rows']}
        for row in result['rows']:
            base=expected_rows[key(row)]
            assert row[6]==pytest.approx(base[6],rel=1e-7,abs=1e-18)
            assert row[7:]==base[7:]
        session=json.dumps(made['session'])
        # Corner-only model edits are invisible to the old nominal snapshot.
        bridge.skill('procedure(matrixTestModel(section) let((db c m) db=axlGetMainSetupDB('+session+') c=axlGetCorner(db "tt") m=axlGetModel(c "gpdk045.scs") axlSetModelSection(m section) maeSaveSetup(?session '+session+') t))')
        bridge.skill('matrixTestModel("ff")')
        assert call('inspect_opamp_matrix',setup_ref=made['setup_ref'])['code']=='reference_changed'
        denied=call('run_opamp_matrix',setup_ref=made['setup_ref'],request_id='edited',gpdk_root=str(ROOT))
        assert denied['state']=='failed' and denied['history'] is None
        bridge.skill('matrixTestModel("tt")')
        assert call('inspect_opamp_matrix',setup_ref=made['setup_ref'])['ok']
        # Exact older result remains available while current setup has an edit.
        bridge.skill('maeSetVar("cload" "1e-13 3e-13" ?session '+session+')')
        assert call('inspect_opamp_matrix',setup_ref=made['setup_ref'])['code']=='reference_changed'
        assert call('read_opamp_matrix_results',run_ref=status['run_ref'])==result
        bridge.skill('progn(maeSetVar("cload" "1e-13 2e-13" ?session '+session+') maeSaveSetup(?session '+session+'))')
        assert call('inspect_opamp_matrix',setup_ref=made['setup_ref'])['ok']
        # Missing and non-scalar measurements must not be reported as measured.
        scalar=json.loads(bridge.skill('aiCircuitJson(list(aiMatrixScalar(nil) aiMatrixScalar(\'Error) aiMatrixScalar(list(1 2)) aiMatrixScalar(0)))'))
        assert scalar==[None,None,None,True]
        assert call('release_opamp_reference',setup_ref=made['setup_ref'])['stage']=='released'
        retained=json.loads(bridge.skill('aiCircuitJson(maeGetSessions())'))
        assert made['session'] in retained and ref['session'] not in retained
        assert snapshot(source)==source_snapshot and snapshot(nominal)==nominal_snapshot
        # A failure after Save As retains the partial target but closes its own
        # bootstrap session; retry must not silently recreate it.
        bridge.skill("matrixTestConfigureOriginal=getd('aiMatrixConfigure)")
        try:
            bridge.skill('procedure(aiMatrixConfigure(amcEntry) error("injected matrix configure failure"))')
            failedargs={**matargs,'cell':'partial_matrix','request_id':'partial'}
            failed=call('create_opamp_matrix',**failedargs)
            assert not failed['ok'] and not failed['saved'] and failed['session'] is None
            assert call('create_opamp_matrix',**failedargs)==failed
        finally:
            bridge.skill("putd('aiMatrixConfigure matrixTestConfigureOriginal)")
        assert json.loads(bridge.skill('aiCircuitJson(maeGetSessions())'))==retained
        assert snapshot(source)==source_snapshot
        assert call('release_opamp_reference',setup_ref=source['setup_ref'])['stage']=='released'
        assert call('release_opamp_reference',setup_ref=nominal['setup_ref'])['stage']=='released'
        assert before==source_hashes()
        (tmp_path/'acceptance.json').write_text(json.dumps(dict(reference=ref_status,rebuilt=status,reference_results=expected,results=result,netlists_equivalent=True),indent=2))
        bridge.skill('hiRegTimer("exit()" 5)'); assert process.wait(timeout=25)==0
    finally:
        os.close(rfd)
        if process and process.poll() is None:
            os.killpg(process.pid,signal.SIGTERM)
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: os.killpg(process.pid,signal.SIGKILL); process.wait(timeout=5)
        xprocess.terminate(); xprocess.wait(timeout=5)
