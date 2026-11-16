"""Real RDB acceptance with arbitrary test/output/corner names and analogLib only."""
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


def test_generic_results_arbitrary_test_names_and_history(tmp_path):
    if os.environ.get('CAD_AI_RUN_GUI_TESTS') != '1': pytest.skip('requires licensed GUI/Spectre')
    virtuoso, xvfb = shutil.which('virtuoso'), shutil.which('Xvfb')
    if not virtuoso or not xvfb: pytest.skip('Virtuoso/Xvfb unavailable')
    tag=uuid.uuid4().hex[:8]; library='resultMcp'+tag
    (tmp_path/'cds.lib').write_text(f'DEFINE analogLib {IC_LIBS}/artist/analogLib\nDEFINE basic {IC_LIBS}/basic\n')
    # No PDK, gate, OpAmp or matrix modules are loaded. RC is only a schematic fixture.
    modules=['AI_protocol','AI_eval','AI_circuit','AI_circuit_context','AI_circuit_task','AI_circuit_rc','AI_maestro_results']
    lines=['load('+json.dumps(str(AI_ROOT/'skill'/(m+'.il')))+')' for m in modules]
    lines += [f'ddCreateLib("{library}" '+json.dumps(str(tmp_path/'library'))+')',
              'envSetVal("asimenv.startup" "projectDir" \'string '+json.dumps(str(tmp_path/'simulation'))+')',
              'procedure(resultTestPoll() when(isFile("command.il") load("command.il") deleteFile("command.il")) hiRegTimer("resultTestPoll()" 1))',
              'resultTestPoll()', 'aiWriteTextFile("ready" "yes")']
    (tmp_path/'run.il').write_text('\n'.join(lines)+'\n')
    rfd,wfd=os.pipe();process=None
    xp=subprocess.Popen([xvfb,'-displayfd',str(wfd),'-screen','0','1440x1000x24','-nolisten','tcp'],pass_fds=(wfd,),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    os.close(wfd)
    try:
        assert select.select([rfd],[],[],10)[0]
        display=os.read(rfd,32).decode().strip();assert display.isdigit()
        with (tmp_path/'process.log').open('w') as log:
            process=subprocess.Popen([virtuoso,'-nocdsinit','-norestore','-replay','run.il','-log','CDS.log'],cwd=tmp_path,
                env=dict(os.environ,DISPLAY=':'+display),stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        bridge=NativeBridge(tmp_path,process);bridge.wait_file(tmp_path/'ready')
        spool=tmp_path/'spool';spool.mkdir(mode=0o700)
        server=McpServer(DispatchClient(RequestDispatcher(bridge,RuntimePaths(tmp_path,tmp_path/'socket',spool),tmp_path)),workspace=tmp_path)
        def call(name,**args):return mcp_call(server,name,args)
        task=call('begin_circuit_task',task_id='result_task',presentation='foreground',choice_confirmed=True)
        pre=call('preview_rc_circuit',library=library,cell='network')
        created=call('create_rc_circuit',library=library,cell='network',task_ref=task['task_ref'],request_id='network',preview_digest=pre['preview_digest'])
        assert created['saved'],created
        test='response_'+tag
        # Explicit fixture data supplies all test names, expressions and corner settings.
        bridge.skill('progn(resultSession=maeOpenSetup("'+library+'" "measurements" "maestro" ?application "Assembler" ?mode "a") '
            'maeCreateTest("'+test+'" ?lib "'+library+'" ?cell "network" ?view "schematic" ?simulator "spectre" ?session resultSession) '
            'maeSetAnalysis("'+test+'" "dc" ?enable t ?options list(list("saveOppoint" t)) ?session resultSession) '
            'maeSetAnalysis("'+test+'" "tran" ?enable t ?options list(list("stop" "8u") list("errpreset" "conservative")) ?session resultSession) '
            'maeAddOutput("plateau" "'+test+'" ?outputType "point" ?expr "VDC(\\\"/OUT\\\")" ?save t ?session resultSession) '
            'maeAddOutput("sample_at_t" "'+test+'" ?outputType "point" ?expr "value(VT(\\\"/OUT\\\") 2.001e-6)" ?save t ?session resultSession) '
            'maeAddOutput("whole_trace" "'+test+'" ?outputType "point" ?expr "VT(\\\"/OUT\\\")" ?save t ?session resultSession) '
            'maeSetCorner("cool" ?enabled t ?session resultSession) maeSetCorner("warm" ?enabled t ?session resultSession) '
            'maeSetVar("temperature" "10" ?typeName "corner" ?typeValue list("cool") ?session resultSession) '
            'maeSetVar("temperature" "60" ?typeName "corner" ?typeValue list("warm") ?session resultSession) '
            'maeSetCorner("Nominal" ?enabled nil ?session resultSession) maeSaveSetup(?session resultSession) '
            'procedure(resultDone(s r) resultFinished=t) resultFinished=nil '
            'resultHistory=maeRunSimulation(?session resultSession ?callback "resultDone" ?waitUntilDone nil) resultHistory)')
        end=time.monotonic()+160
        while time.monotonic()<end:
            if bridge.skill('if(resultFinished "done" "running")')=='done':break
            time.sleep(.5)
        assert bridge.skill('if(resultFinished "done" "running")')=='done'
        history=bridge.skill('resultHistory')
        bridge.skill('maeCloseSession(?session resultSession)')
        background=call('open_design_view',library=library,cell='measurements',view='maestro',oa_view_type='maestro',presentation='background',access='read',request_id='background')
        assert background['ok'],background
        bgresult=call('read_maestro_results',context_ref=background['context_ref'],history=history)
        assert bgresult['ok'],bgresult
        assert call('release_design_context',context_ref=background['context_ref'])['stage']=='released'
        assert background['session'] not in (json.loads(bridge.skill('aiCircuitJson(maeGetSessions())')) or [])
        opened=call('open_design_view',library=library,cell='measurements',view='maestro',oa_view_type='maestro',presentation='foreground',access='read',request_id='read')
        assert opened['ok'],opened
        # Cold historical capture works although axlGetRunStatus can be (0 0).
        setup_files=list((tmp_path/'library/measurements/maestro').glob('*.sdb'))
        before={str(p):p.read_bytes() for p in setup_files}
        result=call('read_maestro_results',context_ref=opened['context_ref'],history=history)
        assert result['ok'] and result['test_points']==2 and result['output_count']==6,result
        assert result['spec_qualified'] is None and result['simulation_status']=='done'
        assert result['result_ref']==bgresult['result_ref']
        assert call('read_maestro_results',context_ref=opened['context_ref'],history=history)['result_ref']==result['result_ref']
        assert call('read_maestro_results',context_ref=opened['context_ref'],history='Missing.999')['ok'] is False
        items=call('query_maestro_results',result_ref=result['result_ref'],limit=100)['items']
        assert {r['test'] for r in items}=={test}
        assert {r['corner'] for r in items}=={'warm','cool'}
        for row in items:
            if row['output']=='whole_trace':assert row['status']=='non_scalar' and row['value'] is None
            elif row['output']=='plateau':assert row['value']==pytest.approx(1.0,abs=.002)
            else:assert row['value']==pytest.approx(pre['expected']['tran_tau_v'],abs=.002)
        expected=[dict(test=test,corner=c,point=1) for c in ('warm','cool')]
        contract=dict(source='Explicit analogLib fixture acceptance',expected_test_points=expected,
                      specifications=[dict(test=test,output='plateau',unit='V',lower=.998,upper=1.002),
                                      dict(test=test,output='sample_at_t',unit='V',lower=.630,upper=.635)])
        passed=call('evaluate_measurement_specs',result_ref=result['result_ref'],contract=contract)
        assert passed['qualification']=='pass' and passed['coverage_complete'],passed
        contract['specifications'][0]['upper']=.5;contract['specifications'][0]['lower']=.1
        failed=call('evaluate_measurement_specs',result_ref=result['result_ref'],contract=contract)
        assert failed['qualification']=='fail' and failed['counts']['fail']==2
        contract['specifications']=[dict(test=test,output='whole_trace',unit='V',lower=0)]
        incomplete=call('evaluate_measurement_specs',result_ref=result['result_ref'],contract=contract)
        assert incomplete['qualification']=='incomplete' and incomplete['spec_qualified'] is None
        assert before=={str(p):p.read_bytes() for p in setup_files}
        # Offline artifacts remain usable after releasing context.
        call('release_design_context',context_ref=opened['context_ref'])
        assert opened['session'] in json.loads(bridge.skill('aiCircuitJson(maeGetSessions())'))
        assert call('query_maestro_results',result_ref=result['result_ref'],entity='definitions')['total']==3
        assert call('read_maestro_results',context_ref=opened['context_ref'],history=history)['ok'] is False
        assert json.loads(bridge.skill('aiCircuitJson(length(aiResultsCaptures))'))==0
        (tmp_path/'acceptance.json').write_text(json.dumps(dict(result=result,passed=passed,failed=failed,incomplete=incomplete),indent=2))
        # Product foreground release intentionally leaves the window alive.
        # This isolated Xvfb process belongs to the test and is terminated below.
        bridge.skill('procedure(resultTestPoll() t)')
    finally:
        os.close(rfd)
        if process and process.poll() is None:
            os.killpg(process.pid,signal.SIGTERM)
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)
        xp.terminate();xp.wait(timeout=5)
